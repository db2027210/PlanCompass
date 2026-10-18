#!/usr/bin/env python3
"""Publish an immutable TPC-H v34 one-round Algorithm C debug bundle.

The bundle keeps the deterministic v33 local data-plane implementation and
resource envelope, replaces only the three optimizer modules needed for the
original-gradient/all-subset/L1-L3 contract, and binds the already validated
v33 R0 evidence into a new launch authority.  The debug entry point still
performs a fresh in-band R0 before the one outer debug round; the historical
v33 result is readiness evidence, never an accepted round.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RAW_ALL_SUBSET_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "source-v24-6e3baf1d78a00ce039491cb44695ad3d8578f8a1950dc4a07bfe0a55d79abd50"
)
READINESS_ROOT = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
BASE_SOURCE = (
    READINESS_ROOT
    / "source-v33-c1b919b73747ef3eddbae9ed8f0427cbd7a954a18c0b88bd26db6a9b038e7e09"
)
PATCH_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/job_boundary_bundle_v1"
    / (
        "source-57b0951a841a-37c8d90ea999-18a59a9aaf5e-"
        "178463c4e6b7-4d65ef444380-0c6ad0f42fea-"
        "fresh-r0-no-historical-authority-v1"
    )
)
V33_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_v33_r0"
    / "3e52fd3e424fbef8fae77bc022d5ed198d202a3c937f237fa4b7f5689f602229"
)
V33_R0_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v33_cpu450_io576_terminal4_deterministic_v1"
)
V33_R0_POINT = (
    V33_R0_ROOT
    / "production_points/alg_c_tpch_r0_20260729T133913_697ed2c7"
)
BASE_READINESS = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
BASE_METHOD = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_recovery_journal_v2"
    / "method-b9f332b5fa1b6f6421e2"
    / "experience_table_sgd_method_manifest.json"
)
ROOT_VERIFIER = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
SOURCE_DESTINATION = READINESS_ROOT
BUNDLE_DESTINATION = ROOT / "artifacts/mysql_tpch_v34_debug"
SUPERSEDES_BUNDLE_IDS = [
    "479ae782242323974f2317c674ce02ab13ce487b4f14ecd9a598f9995e8977c9",
    "9567b3bd994b6522b6cd35d291265ea1ea9eaf9359993bb64c09f880fcb4a0f4",
]
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_v34_original_gradient_boundary_debug_20260729"
)
FAILED_V34_R0_POINT = (
    OUTPUT_ROOT
    / "gradient_debug_rounds/20260729T082653Z/production_points/"
    "alg_c_tpch_r0_20260729T162653_a06fc61f"
)
DIAGNOSTIC_V34_R0_POINT = (
    OUTPUT_ROOT
    / "gradient_debug_rounds/20260729T101104Z/production_points/"
    "alg_c_tpch_r0_20260729T181104_4a8f2948"
)
DIAGNOSTIC_FORMAL_START_EPOCH_MS = 1785320679080
DIAGNOSTIC_INTERFERENCE_EPOCH_MS = 1785321141275
RECALIBRATED_CPU_CORES = 4.0
RECALIBRATED_IO_BPS = 100_663_296

PATCHED_MODULES = {
    "gradient_descent_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "gradient_descent_optimizer.py",
    "experience_table_subset_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "experience_table_subset_optimizer.py",
    "mysql_parameter_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_parameter_optimizer.py",
    "formal_mysql_sgd_campaign.py": ROOT / "formal_mysql_sgd_campaign.py",
    "mysql_algorithm_c_local_services.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "shell/mysql_algorithm_c_local_services_v25.py":
        ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
    "mysql_process_classification.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    "shell/mysql_process_classification_v10.py":
        ROOT / "shell/mysql_process_classification_v10.py",
    "mysql_plan_clustering.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_plan_clustering.py",
    "mysql_three_level_fingerprint.py":
        RAW_ALL_SUBSET_SOURCE / "mysql_three_level_fingerprint.py",
    "test_debug_mysql_gradient_round.py":
        PATCH_SOURCE / "test_debug_mysql_gradient_round.py",
    "test_experience_table_gradient_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "test_experience_table_gradient_optimizer.py",
    "test_experience_table_subset_optimizer.py":
        RAW_ALL_SUBSET_SOURCE / "test_experience_table_subset_optimizer.py",
    "test_mysql_three_level_fingerprint.py":
        RAW_ALL_SUBSET_SOURCE / "test_mysql_three_level_fingerprint.py",
}
EXTRA_SOURCE_FILES = {
    (
        "config/formal/mysql_sgd_production/profiles/"
        f"{scenario}.json"
    ): (
        ROOT
        / "config/formal/mysql_sgd_production/profiles"
        / f"{scenario}.json"
    )
    for scenario in (
        "job_memory",
        "tpch_memory",
        "tpcc_cpu",
        "hybench_xp_io",
    )
}
EXTRA_SOURCE_FILES.update(
    {
        "shell/mysql_algorithm_c_local_services_v26.py":
            ROOT / "shell/mysql_algorithm_c_local_services_v26.py",
        "tests/test_mysql_parameter_optimizer_baseline_binding.py":
            ROOT / "tests/test_mysql_parameter_optimizer_baseline_binding.py",
        "tests/test_formal_runtime_estimator_baseline.py":
            ROOT / "tests/test_formal_runtime_estimator_baseline.py",
        "tests/test_mysql_local_preparation_interference_gate.py":
            ROOT / "tests/test_mysql_local_preparation_interference_gate.py",
        "tests/test_mysql_process_classification_v10.py":
            ROOT / "tests/test_mysql_process_classification_v10.py",
        "tests/test_mysql_benchmark_monitor_observer_signature_v1.py":
            ROOT
            / "tests/test_mysql_benchmark_monitor_observer_signature_v1.py",
        "tests/test_tpch_v34_recalibration_derivation_v4.py":
            ROOT / "tests/test_tpch_v34_recalibration_derivation_v4.py",
    }
)


def canonical(value: Any) -> bytes:
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


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def source_payload(relative: str, path: Path) -> bytes:
    payload = path.read_bytes()
    if relative != "mysql_parameter_optimizer.py":
        return payload
    text = payload.decode("utf-8")
    anchor = """FORMAL_FROZEN_ACTIVE = {
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_ACTIVE,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V3_ACTIVE,
}
"""
    if text.count(anchor) != 1:
        raise RuntimeError("TPC-H optimizer freeze registry anchor differs")
    tpch_freeze = (
        "frozen_mysql_tpch_production_representatives_v11_"
        "d9c7309763779cd1d6a9"
    )
    replacement = f"""FORMAL_FROZEN_ACTIVE = {{
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_ACTIVE,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V3_ACTIVE,
    {tpch_freeze!r}: {{"tpch": copy.deepcopy(FROZEN_MYSQL_V3_ACTIVE["tpch"])}},
}}
"""
    text = text.replace(anchor, replacement, 1)
    range_anchor = """FORMAL_FROZEN_RANGES = {
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_RANGES,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V2_RANGES,
}
"""
    if text.count(range_anchor) != 1:
        raise RuntimeError("TPC-H optimizer range registry anchor differs")
    range_replacement = f"""FORMAL_FROZEN_RANGES = {{
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_RANGES,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V2_RANGES,
    {tpch_freeze!r}: FROZEN_MYSQL_V2_RANGES,
}}
"""
    return text.replace(
        range_anchor, range_replacement, 1
    ).encode("utf-8")


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def pointer(path: Path, *, role: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"authority input is absent or symlinked: {resolved}")
    return {
        "path": str(resolved),
        "sha256": digest(resolved),
        "role": role,
        "scenario_scope": ["tpch_memory"],
    }


def _json_lines(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def diagnostic_demand_evidence() -> dict[str, Any]:
    """Validate and summarize only the clean pre-interference diagnostic."""
    runtime_gate_path = (
        DIAGNOSTIC_V34_R0_POINT / "runtime_interference_gate_v1.json"
    )
    readback_path = (
        DIAGNOSTIC_V34_R0_POINT / "effective_resource_readback_v2.json"
    )
    resource_path = DIAGNOSTIC_V34_R0_POINT / "resource_samples.jsonl"
    database_path = DIAGNOSTIC_V34_R0_POINT / "database_samples.jsonl"
    workload_path = DIAGNOSTIC_V34_R0_POINT / "workload.stdout.log"
    for path in (
        runtime_gate_path,
        readback_path,
        resource_path,
        database_path,
        workload_path,
    ):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(
                f"TPC-H diagnostic input is absent or symlinked: {path}"
            )

    gate = json.loads(runtime_gate_path.read_text(encoding="utf-8"))
    first_violation = gate.get("first_violation") or {}
    if (
        gate.get("schema_version")
        != "mysql_local_runtime_interference_gate_v1"
        or gate.get("scenario") != "tpch_memory"
        or gate.get("passed") is not False
        or gate.get("status") != "INTERFERENCE_DETECTED"
        or first_violation.get("epoch_ms")
        != DIAGNOSTIC_INTERFERENCE_EPOCH_MS
        or not first_violation.get("violations")
    ):
        raise RuntimeError("TPC-H diagnostic fail-closed evidence changed")

    readback = json.loads(readback_path.read_text(encoding="utf-8"))
    effective = readback.get("effective") or {}
    if (
        readback.get("verified") is not True
        or readback.get("formal_scope") != "mysql_only"
        or effective.get("cpu_quota_us") != 650000
        or effective.get("io_read_bps") != RECALIBRATED_IO_BPS
        or effective.get("io_write_bps") != RECALIBRATED_IO_BPS
    ):
        raise RuntimeError("TPC-H diagnostic source envelope changed")

    resource_rows = [
        row
        for row in _json_lines(resource_path)
        if (
            DIAGNOSTIC_FORMAL_START_EPOCH_MS
            <= int(row["epoch_ms"])
            < DIAGNOSTIC_INTERFERENCE_EPOCH_MS
        )
    ]
    if (
        len(resource_rows) != 462
        or resource_rows[0]["epoch_ms"] != 1785320679586
        or resource_rows[-1]["epoch_ms"] != 1785321141126
    ):
        raise RuntimeError("TPC-H formal-active diagnostic window changed")

    def mean(name: str) -> float:
        return sum(float(row[name]) for row in resource_rows) / len(
            resource_rows
        )

    cpu_mean = mean("cpu_utilization_ratio")
    memory_mean = mean("memory_utilization_ratio")
    io_mean = mean("io_utilization_ratio")
    if (
        abs(cpu_mean - 0.2450616546302356) > 1e-12
        or abs(memory_mean - 0.899551846802987) > 1e-12
        or abs(io_mean - 0.45057753143752005) > 1e-12
    ):
        raise RuntimeError("TPC-H diagnostic utilization changed")

    database_rows = [
        row
        for row in _json_lines(database_path)
        if (
            DIAGNOSTIC_FORMAL_START_EPOCH_MS
            <= int(row["epoch_ms"])
            < DIAGNOSTIC_INTERFERENCE_EPOCH_MS
        )
    ]
    database_summary: dict[str, Any] = {}
    for sampler_index, expected_count in ((0, 441), (1, 438)):
        rows = [
            row
            for row in database_rows
            if int(row["sampler_index"]) == sampler_index
        ]
        gaps = [
            (int(right["epoch_ms"]) - int(left["epoch_ms"])) / 1000.0
            for left, right in zip(rows, rows[1:])
        ]
        coverage = len(rows) / len(resource_rows)
        maximum_gap = max(gaps)
        if (
            len(rows) != expected_count
            or rows[0]["completed_total"] != 43
            or rows[-1]["completed_total"] != 60
            or coverage < 0.90
            or maximum_gap > 10.0
        ):
            raise RuntimeError(
                f"TPC-H database diagnostic sampler {sampler_index} changed"
            )
        database_summary[str(sampler_index)] = {
            "sample_count": len(rows),
            "coverage_ratio": coverage,
            "maximum_gap_seconds": maximum_gap,
            "first_completed_total": rows[0]["completed_total"],
            "last_completed_total": rows[-1]["completed_total"],
        }

    client_samples = []
    prefix = "BENCHBASE_FORMAL_SAMPLE_JSON="
    for line in workload_path.read_text(encoding="utf-8").splitlines():
        if prefix not in line:
            continue
        sample = json.loads(line.split(prefix, 1)[1])
        if (
            DIAGNOSTIC_FORMAL_START_EPOCH_MS
            <= int(sample["epoch_ms"])
            < DIAGNOSTIC_INTERFERENCE_EPOCH_MS
            and sample.get("measurement_phase") is True
        ):
            client_samples.append(sample)
    if (
        len(client_samples) != 458
        or max(row["completed_total"] for row in client_samples) != 1
    ):
        raise RuntimeError("TPC-H client completion diagnostic changed")

    measured_cpu_demand = cpu_mean * 6.5
    projected_cpu_utilization = (
        measured_cpu_demand / RECALIBRATED_CPU_CORES
    )
    if not 0.30 < projected_cpu_utilization < 0.50:
        raise RuntimeError("TPC-H recalibrated CPU projection is outside band")
    return {
        "schema_version":
            "mysql_tpch_v34_pre_interference_demand_diagnostic_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "valid_r0": False,
        "accepted_round_authorized": False,
        "reason_not_r0":
            "runtime interference gate detected external overlapping PG "
            "after the diagnostic cutoff",
        "window": {
            "start_epoch_ms": DIAGNOSTIC_FORMAL_START_EPOCH_MS,
            "exclusive_end_epoch_ms": DIAGNOSTIC_INTERFERENCE_EPOCH_MS,
            "duration_seconds": (
                int(resource_rows[-1]["epoch_ms"])
                - int(resource_rows[0]["epoch_ms"])
            ) / 1000.0,
            "resource_sample_count": len(resource_rows),
            "selection":
                "all formal-active resource samples before first violation",
        },
        "source_envelope": {
            "cpu_quota_cores": 6.5,
            "io_read_bps": RECALIBRATED_IO_BPS,
            "io_write_bps": RECALIBRATED_IO_BPS,
        },
        "observed_utilization": {
            "cpu": cpu_mean,
            "memory": memory_mean,
            "io": io_mean,
        },
        "database_counter": {
            "counter_name": "Com_select",
            "samplers": database_summary,
        },
        "client_completion": {
            "sample_count": len(client_samples),
            "coverage_ratio": len(client_samples) / len(resource_rows),
            "completed_total": 1,
        },
        "measured_cpu_demand_cores": measured_cpu_demand,
        "target_non_target_band": [0.3, 0.5],
        "new_cpu_quota_cores": RECALIBRATED_CPU_CORES,
        "projected_cpu_utilization": projected_cpu_utilization,
        "new_io_bps_per_direction": RECALIBRATED_IO_BPS,
        "source": {
            "runtime_interference_gate": {
                "path": str(runtime_gate_path),
                "sha256": digest(runtime_gate_path),
            },
            "effective_resource_readback": {
                "path": str(readback_path),
                "sha256": digest(readback_path),
            },
            "resource_samples": {
                "path": str(resource_path),
                "sha256": digest(resource_path),
            },
            "database_samples": {
                "path": str(database_path),
                "sha256": digest(database_path),
            },
            "workload_stdout": {
                "path": str(workload_path),
                "sha256": digest(workload_path),
            },
        },
    }


def validate_v33_inputs(
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for path in (
        BASE_SOURCE / "source_manifest.json",
        V33_BUNDLE / "launch_manifest.json",
        V33_BUNDLE / "config.json",
        V33_BUNDLE / "profile.json",
        V33_R0_ROOT / "v33_r0_result.json",
        V33_R0_ROOT / "v33_r0_driver_state.json",
        V33_R0_POINT / "campaign_runtime.json",
        V33_R0_POINT / "effective_resource_readback_v2.json",
        V33_R0_POINT / "r0_result.json",
        BASE_READINESS,
        BASE_METHOD,
        ROOT_VERIFIER,
        *PATCHED_MODULES.values(),
        *EXTRA_SOURCE_FILES.values(),
    ):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"required input is absent or symlinked: {path}")

    source_manifest = json.loads(
        (BASE_SOURCE / "source_manifest.json").read_text(encoding="utf-8")
    )
    if (
        source_manifest.get("schema_version")
        != "mysql_tpch_local_deterministic_source_v33"
        or source_manifest.get("bundle_id")
        != BASE_SOURCE.name.removeprefix("source-v33-")
        or source_manifest.get("formal_scope") != "mysql_only"
        or source_manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v33 source identity or scope differs")
    for relative, expected in source_manifest["files"].items():
        candidate = BASE_SOURCE / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != expected
        ):
            raise RuntimeError(f"v33 source closure mismatch: {relative}")

    launch = json.loads(
        (V33_BUNDLE / "launch_manifest.json").read_text(encoding="utf-8")
    )
    if (
        launch.get("bundle_id") != V33_BUNDLE.name
        or launch.get("formal_scope") != "mysql_only"
        or launch.get("fresh_r0_authorized") is not True
        or launch.get("outer_execution_authorized") is not False
    ):
        raise RuntimeError("v33 launch manifest identity/scope differs")
    r0 = json.loads(
        (V33_R0_ROOT / "v33_r0_result.json").read_text(encoding="utf-8")
    )
    expected_utilization = {
        "cpu": 0.3791698993233116,
        "memory": 0.9118560290021497,
        "io": 0.4024884938626553,
    }
    if (
        r0.get("stable") is not True
        or r0.get("correctness_passed") is not True
        or r0.get("utilization_pct") != expected_utilization
    ):
        raise RuntimeError("v33 R0 is not the validated readiness result")
    readback = json.loads(
        (V33_R0_POINT / "effective_resource_readback_v2.json").read_text(
            encoding="utf-8"
        )
    )
    effective = readback.get("effective") or {}
    envelope = launch.get("resource_envelope") or {}
    required_equal = {
        "cpu_quota_cores": float(effective.get("cpu_quota_us", 0)) / 100000.0,
        "memory_bytes": effective.get("memory_bytes"),
        "memory_swap_bytes": effective.get("memory_swap_bytes"),
        "io_read_bps": effective.get("io_read_bps"),
        "io_write_bps": effective.get("io_write_bps"),
        "pids_max": effective.get("pids_max"),
        "cpuset_cpus": effective.get("cpuset_cpus"),
        "cpuset_mems": effective.get("cpuset_mems"),
    }
    for name, observed in required_equal.items():
        if envelope.get(name) != observed:
            raise RuntimeError(
                f"v33 R0 effective envelope mismatch for {name}: "
                f"manifest={envelope.get(name)!r}, effective={observed!r}"
            )
    if effective.get("storage_device") != envelope.get("device"):
        raise RuntimeError("v33 R0 storage device differs from launch manifest")
    failed_v34 = json.loads(
        (FAILED_V34_R0_POINT / "r0_result.json").read_text(encoding="utf-8")
    )
    failed_gate = failed_v34.get("r0_profile_gate") or {}
    expected_failed_utilization = {
        "cpu": 0.6475398517695004,
        "memory": 0.9032126942766139,
        "io": 0.04559699845929334,
    }
    selected = (
        (failed_v34.get("rolling_measurement_control") or {})
        .get("selected_observation") or {}
    )
    if (
        failed_gate.get("passed") is not False
        or failed_gate.get("utilization") != expected_failed_utilization
        or selected.get("resource_sample_coverage") != 1
        or not (selected.get("database_throughput") or {}).get("reliable")
        or not (selected.get("stability") or {}).get("stable")
    ):
        raise RuntimeError("v34 failed R0 demand evidence is absent or changed")
    return source_manifest, launch, diagnostic_demand_evidence()


def publish_source(
    base_manifest: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
) -> tuple[Path, dict[str, str], str]:
    files: dict[str, str] = {}
    for relative, expected in sorted(base_manifest["files"].items()):
        source_path = PATCHED_MODULES.get(relative, BASE_SOURCE / relative)
        files[relative] = digest_bytes(source_payload(relative, source_path))
        if relative not in PATCHED_MODULES and files[relative] != expected:
            raise RuntimeError(f"unpatched v33 source changed: {relative}")
    profile = json.loads(
        (V33_BUNDLE / "profile.json").read_text(encoding="utf-8")
    )
    profile.update(
        {
            "cpu_cores": RECALIBRATED_CPU_CORES,
            "read_bps": RECALIBRATED_IO_BPS,
            "write_bps": RECALIBRATED_IO_BPS,
            "derivation": {
                "schema_version":
                    "mysql_tpch_v34_measured_demand_recalibration_v4",
                "diagnostic_evidence_sha256":
                    digest_bytes(canonical(diagnostic)),
                "source_envelope": diagnostic["source_envelope"],
                "observed_utilization":
                    diagnostic["observed_utilization"],
                "target_non_target_band": [0.3, 0.5],
                "cpu_method":
                    "all 462 formal-active samples before first external "
                    "interference: 0.2450616546302356 * 6.5 = "
                    "1.5929007550965315 demand cores; divide by 4.0",
                "io_method":
                    "retain measured 96MiB/s symmetric caps; pre-interference "
                    "formal IO mean 0.45057753143752005 and client completion "
                    "total reached 1",
                "new_cpu_cores": RECALIBRATED_CPU_CORES,
                "new_io_bps_per_direction": RECALIBRATED_IO_BPS,
                "new_terminals_actual": 4,
                "failed_v34_result_not_reused_as_r0": True,
                "interfered_diagnostic_not_reused_as_r0": True,
            },
        }
    )
    profile_payload = canonical(profile)
    files["profiles/tpch_v34_debug_profile.json"] = digest_bytes(profile_payload)
    for relative, source_path in sorted(EXTRA_SOURCE_FILES.items()):
        files[relative] = digest(source_path)
    identity = {
        "schema_version": "mysql_tpch_v34_original_gradient_boundary_source_v5",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "base_source_bundle_id": base_manifest["bundle_id"],
        "patch_source": str(PATCH_SOURCE),
        "patched_modules": {
            name: files[name] for name in sorted(PATCHED_MODULES)
        },
        "runtime_contract": {
            "database_counter_source":
                "SHOW GLOBAL STATUS Com_select on two staggered persistent sessions",
            "database_counter_timeout_seconds": 3,
            "database_counter_minimum_coverage": 0.90,
            "database_counter_maximum_gap_seconds": 10,
            "database_counter_timeout_fail_closed": True,
            "late_process_interference_gate":
                "one_second_runtime_cpuset_overlap_fail_closed",
        },
        "recalibration_diagnostic_sha256":
            digest_bytes(canonical(diagnostic)),
        "files": files,
    }
    source_id = digest_bytes(canonical(identity))
    final = SOURCE_DESTINATION / f"source-v34-{source_id}"
    if final.exists():
        manifest = json.loads(
            (final / "source_manifest.json").read_text(encoding="utf-8")
        )
        if manifest != {**identity, "bundle_id": source_id}:
            raise RuntimeError(f"existing v34 source identity differs: {final}")
        for relative, expected in files.items():
            candidate = final / relative
            if candidate.is_symlink() or digest(candidate) != expected:
                raise RuntimeError(f"existing v34 source content differs: {relative}")
        return final, files, source_id

    stage = SOURCE_DESTINATION / f".source-v34-{source_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative in sorted(base_manifest["files"]):
            source_path = PATCHED_MODULES.get(relative, BASE_SOURCE / relative)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_payload(relative, source_path))
        for relative, source_path in sorted(EXTRA_SOURCE_FILES.items()):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
        profile_path = stage / "profiles/tpch_v34_debug_profile.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_bytes(profile_payload)
        (stage / "source_manifest.json").write_bytes(
            canonical({**identity, "bundle_id": source_id})
        )
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, files, source_id


def launcher_bytes(source: Path) -> bytes:
    debug = source / "shell/debug_mysql_gradient_round_v25.py"
    return f"""#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

ROOT = Path({str(ROOT)!r})
SOURCE = Path({str(source)!r})
VERIFIER = Path({str(ROOT_VERIFIER)!r})
DEBUG = Path({str(debug)!r})
os.chdir("/tmp")
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell"), str(ROOT / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen root readiness verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(ROOT), str(SOURCE))
]
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def build_config(source: Path) -> bytes:
    config = json.loads((V33_BUNDLE / "config.json").read_text(encoding="utf-8"))
    config.update(
        {
            "exp_id": "mysql_algorithm_c.tpch_memory.v34_original_gradient_debug",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "fresh_r0_only": False,
            "outer_execution_authorized": True,
            "accepted_round_authorized": False,
            "profile": str(
                source / "profiles/tpch_v34_debug_profile.json"
            ),
        }
    )
    config["output"]["directory"] = str(OUTPUT_ROOT)
    config["minimum_measurement_seconds"] = 180
    config["workload"]["v34_resource_contract"] = {
        "cpu_quota_cores": RECALIBRATED_CPU_CORES,
        "io_read_bps": RECALIBRATED_IO_BPS,
        "io_write_bps": RECALIBRATED_IO_BPS,
        "terminals": 4,
        "derivation":
            "pre_interference_measured_cpu_demand_recalibration_v4",
    }
    optimizer = config.setdefault("optimizer", {})
    optimizer.update(
        {
            "original_gradient_only": True,
            "boundary_aware_eta_enabled": True,
            "boundary_search_to_param_limit_enabled": True,
            "boundary_probe_max_steps": 32,
            "boundary_eta_growth_factor": 2.0,
            "plateau_exploration_enabled": False,
            "min_param_delta_rel_pct": 5.0,
            "max_param_delta_rel_pct": 32.0,
            "plateau_score_epsilon": 1e-12,
            "subset_max_workers": 1,
        }
    )
    config["early_stop_source_bundle"]["source"] = str(source)
    config["early_stop_source_bundle"]["source_manifest_sha256"] = digest(
        source / "source_manifest.json"
    )
    config["production"]["launch_authorized"] = True
    config["production"]["service_factory"] = (
        "shell.mysql_algorithm_c_local_services_v26:build"
    )
    config["workload_registry"].update(
        {
            "authorization_state": "AUTHORIZED_BY_V33_FRESH_R0_AND_V34_READINESS",
            "execution_authorized": True,
        }
    )
    return canonical(config)


def verify_readiness(path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    try:
        from shell.verify_mysql_sgd_v4_readiness import verify_document
    finally:
        sys.path.pop(0)
    document = json.loads(path.read_text(encoding="utf-8"))
    checks = verify_document(document, ROOT)
    failed = [
        {
            "requirement": check.requirement,
            "item": check.item,
            "reason": check.reason,
        }
        for check in checks
        if not check.passed
    ]
    if failed:
        raise RuntimeError(f"published readiness failed closed: {failed[:8]}")
    return {"verifier": "PASS", "passed": len(checks), "failed": 0}


def main() -> int:
    base_manifest, v33_launch, diagnostic = validate_v33_inputs()
    source, source_files, source_id = publish_source(
        base_manifest, diagnostic
    )
    config_payload = build_config(source)
    launcher_payload = launcher_bytes(source)
    closure = {
        "schema_version": "mysql_tpch_v34_debug_bundle_closure_v6",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": SUPERSEDES_BUNDLE_IDS,
        "runtime_interference_gate_revision": 2,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "source_bundle_id": source_id,
        "source_manifest_sha256": digest(source / "source_manifest.json"),
        "config_sha256": digest_bytes(config_payload),
        "profile_sha256": digest(
            source / "profiles/tpch_v34_debug_profile.json"
        ),
        "launcher_sha256": digest_bytes(launcher_payload),
        "root_verifier_sha256": digest(ROOT_VERIFIER),
        "base_readiness_sha256": digest(BASE_READINESS),
        "base_method_sha256": digest(BASE_METHOD),
        "v33_launch_manifest_sha256": digest(V33_BUNDLE / "launch_manifest.json"),
        "v33_r0_result_sha256": digest(V33_R0_ROOT / "v33_r0_result.json"),
        "v33_r0_driver_state_sha256": digest(
            V33_R0_ROOT / "v33_r0_driver_state.json"
        ),
        "failed_v34_r0_result_sha256": digest(
            FAILED_V34_R0_POINT / "r0_result.json"
        ),
        "recalibration_diagnostic_sha256":
            digest_bytes(canonical(diagnostic)),
        "readiness_evidence_revision": 7,
    }
    bundle_id = digest_bytes(canonical(closure))
    final = BUNDLE_DESTINATION / bundle_id
    if final.exists():
        readiness_paths = sorted(final.glob("readiness_manifest-*.json"))
        if len(readiness_paths) != 1:
            raise RuntimeError(f"existing v34 bundle is incomplete: {final}")
        result = verify_readiness(readiness_paths[0])
        print(
            json.dumps(
                {
                    "status": "EXISTS_VERIFIED",
                    "bundle_id": bundle_id,
                    "source_bundle_id": source_id,
                    "directory": str(final),
                    "config": str(final / "config.json"),
                    "readiness_manifest": str(readiness_paths[0]),
                    "launcher": str(final / "launch_tpch_v34_debug.py"),
                    **result,
                },
                sort_keys=True,
            )
        )
        return 0

    BUNDLE_DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_DESTINATION / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        (stage / "config.json").write_bytes(config_payload)
        launcher_path = stage / "launch_tpch_v34_debug.py"
        launcher_path.write_bytes(launcher_payload)
        launcher_path.chmod(0o555)

        final_launcher = final / launcher_path.name
        final_config = final / "config.json"
        normalized_envelope = {
            "cpu_isolation_degree": 16,
            "cpu_quota_cores": RECALIBRATED_CPU_CORES,
            "io_read_bps": RECALIBRATED_IO_BPS,
            "io_write_bps": RECALIBRATED_IO_BPS,
            "memory_bytes": 5583454208,
            # The host has swap disabled and no memsw controller.  The
            # normalized contract records the memory ceiling as the effective
            # no-extra-swap ceiling; the raw readback below preserves the
            # kernel-reported zero and its explicit mode.
            "memory_swap_bytes": 5583454208,
            "numa_isolation_degree": 1,
            "pids_max": 512,
        }
        final_source_readback = final / "tpch_v34_source_mysql_readback.json"
        source_readback_payload = canonical(
            {
                "schema_version":
                    "mysql_normalized_resource_envelope_readback_v1",
                "scenario": "tpch_memory",
                "engine": "mysql",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "normalized_envelope": normalized_envelope,
                "raw_effective_readback": {
                    "path": str(
                        V33_R0_POINT / "effective_resource_readback_v2.json"
                    ),
                    "sha256": digest(
                        V33_R0_POINT / "effective_resource_readback_v2.json"
                    ),
                },
                "swap_normalization": {
                    "raw_memory_swap_bytes": 0,
                    "normalized_memory_swap_bytes": 5583454208,
                    "mode": "host_swap_disabled_no_memsw_controller",
                    "additional_swap_available": False,
                },
            }
        )
        (stage / final_source_readback.name).write_bytes(source_readback_payload)

        freeze_id = (
            "mysql_tpch_v34_debug_envelope_recalibrated_from_failed_v34_r0"
        )
        final_scope_readback = final / "tpch_v34_mysql_only_envelope_readback.json"
        scope_readback_payload = canonical(
            {
                "schema_version": "mysql_only_scope_bound_envelope_readback_v1",
                "scenario": "tpch_memory",
                "engine": "mysql",
                "formal_scope_name": "mysql_only",
                "freeze_id": freeze_id,
                "postgresql_counterpart_required": False,
                "cross_engine_envelope_equality_required": False,
                "cross_engine_comparison_prohibited": True,
                "normalized_envelope": normalized_envelope,
                "source_bytes_unchanged": True,
                "source_mysql_readback": {
                    "path": str(final_source_readback),
                    "sha256": digest_bytes(source_readback_payload),
                },
                "status": "SCOPE_BOUND_FROM_AUTHENTIC_MYSQL_READBACK",
                "new_envelope_requires_fresh_in_band_r0": True,
            }
        )
        (stage / final_scope_readback.name).write_bytes(scope_readback_payload)

        point_result = json.loads(
            (V33_R0_POINT / "r0_result.json").read_text(encoding="utf-8")
        )
        selected = point_result["rolling_measurement_control"][
            "selected_observation"
        ]
        final_r0_evidence = final / "tpch_v33_r0_calibration_evidence.json"
        r0_evidence_payload = canonical(
            {
                "schema_version": "mysql_sgd_r0_calibration_evidence_v1",
                "scenario": "tpch_memory",
                "benchmark": "tpch",
                "target_resource": "memory",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "passed": True,
                "historical_readiness_only": True,
                "new_envelope_requires_fresh_in_band_r0": True,
                "latest_window_seconds": 180,
                "latest_window_count": 6,
                "sample_coverage": selected["resource_sample_coverage"],
                "resource_utilization": selected["resource_means"],
                "selected_measurement_seconds":
                    point_result["rolling_measurement_control"][
                        "selected_measurement_seconds"
                    ],
                "stop_reason":
                    point_result["rolling_measurement_control"]["stop_reason"],
                "stability": selected["stability"],
                "correctness": point_result["correctness"],
                "resource_envelope_normalized": normalized_envelope,
                "source": {
                    "v33_r0_result": {
                        "path": str(V33_R0_ROOT / "v33_r0_result.json"),
                        "sha256": digest(V33_R0_ROOT / "v33_r0_result.json"),
                    },
                    "campaign_runtime": {
                        "path": str(V33_R0_POINT / "campaign_runtime.json"),
                        "sha256": digest(
                            V33_R0_POINT / "campaign_runtime.json"
                        ),
                    },
                    "r0_result": {
                        "path": str(V33_R0_POINT / "r0_result.json"),
                        "sha256": digest(V33_R0_POINT / "r0_result.json"),
                    },
                    "effective_resource_readback": {
                        "path": str(
                            V33_R0_POINT
                            / "effective_resource_readback_v2.json"
                        ),
                        "sha256": digest(
                            V33_R0_POINT
                            / "effective_resource_readback_v2.json"
                        ),
                    },
                },
            }
        )
        (stage / final_r0_evidence.name).write_bytes(r0_evidence_payload)
        final_diagnostic = (
            final / "tpch_v34_recalibration_diagnostic_evidence.json"
        )
        diagnostic_payload = canonical(diagnostic)
        (stage / final_diagnostic.name).write_bytes(diagnostic_payload)

        entries = [
            {
                "path": str(source / relative),
                "sha256": expected,
                "role": "source",
                "scenario_scope": ["tpch_memory"],
            }
            for relative, expected in sorted(source_files.items())
        ]
        entries.extend(
            [
                pointer(source / "source_manifest.json", role="source_manifest"),
                {
                    "path": str(final_config),
                    "sha256": digest_bytes(config_payload),
                    "role": "debug_config_authority",
                    "scenario_scope": ["tpch_memory"],
                },
                {
                    "path": str(final_launcher),
                    "sha256": digest_bytes(launcher_payload),
                    "role": "launch_wrapper",
                    "scenario_scope": ["tpch_memory"],
                },
                pointer(ROOT_VERIFIER, role="root_evidence_verifier"),
                pointer(
                    V33_R0_ROOT / "v33_r0_result.json",
                    role="validated_fresh_r0_result",
                ),
                pointer(
                    V33_R0_ROOT / "v33_r0_driver_state.json",
                    role="validated_fresh_r0_driver_state",
                ),
                pointer(
                    V33_R0_POINT / "effective_resource_readback_v2.json",
                    role="validated_fresh_r0_effective_readback",
                ),
                {
                    "path": str(final_source_readback),
                    "sha256": digest_bytes(source_readback_payload),
                    "role": "normalized_source_mysql_readback",
                    "scenario_scope": ["tpch_memory"],
                },
                {
                    "path": str(final_scope_readback),
                    "sha256": digest_bytes(scope_readback_payload),
                    "role": "mysql_only_scope_bound_envelope_readback",
                    "scenario_scope": ["tpch_memory"],
                },
                {
                    "path": str(final_r0_evidence),
                    "sha256": digest_bytes(r0_evidence_payload),
                    "role": "validated_fresh_r0_calibration_evidence",
                    "scenario_scope": ["tpch_memory"],
                },
                {
                    "path": str(final_diagnostic),
                    "sha256": digest_bytes(diagnostic_payload),
                    "role":
                        "invalid_r0_pre_interference_recalibration_diagnostic",
                    "scenario_scope": ["tpch_memory"],
                },
            ]
        )

        method = json.loads(BASE_METHOD.read_text(encoding="utf-8"))
        method["registered_sources"] = entries
        method["formal_launch_authorized"] = True
        method["formal_scope"] = "mysql_only"
        method["cross_engine_comparison_authorized"] = False
        method["runtime_binding"] = {
            "entrypoint": str(final_launcher),
            "source_directory": str(source),
            "config": str(final_config),
            "profile": str(source / "profiles/tpch_v34_debug_profile.json"),
            "service_factory":
                "shell.mysql_algorithm_c_local_services_v26:build",
            "accepted_round_authorized": False,
            "debug_outer_attempts": 1,
        }
        method_payload = canonical(method)
        method_path = stage / "experience_table_sgd_method_manifest.json"
        method_path.write_bytes(method_payload)

        readiness = json.loads(BASE_READINESS.read_text(encoding="utf-8"))
        bundle = readiness["global_requirements"]["method_bundle"]
        bundle["ready"] = True
        bundle["registered_sources"] = [
            {"path": entry["path"], "sha256": entry["sha256"]}
            for entry in entries
        ]
        bundle["method_manifest"] = {
            "path": str(final / method_path.name),
            "sha256": digest_bytes(method_payload),
        }
        tpch = readiness["scenarios"]["tpch_memory"]
        effective = json.loads(
            (V33_R0_POINT / "effective_resource_readback_v2.json").read_text(
                encoding="utf-8"
            )
        )["effective"]
        tpch["environment"].update(
            {
                "current_state":
                    "local_v34_measured_demand_recalibration_pending_fresh_r0",
                "device": effective["storage_device"],
                "host_address": "127.0.0.1",
                "host_id": "compute00",
                "mount": "/disk2/ra_mysql_isolated_v2",
            }
        )
        tpch["mysql_envelope"] = {
            "freeze_id": freeze_id,
            "normalized": normalized_envelope,
            "readback": {
                "path": str(final_scope_readback),
                "sha256": digest_bytes(scope_readback_payload),
            },
        }
        tpch["r0_calibration"] = {
            "passed": True,
            "evidence": {
                "path": str(final_r0_evidence),
                "sha256": digest_bytes(r0_evidence_payload),
            },
        }
        readiness.update(
            {
                "status": "READY",
                "launch_authorized": True,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "debug_authority": {
                    "schema_version": "mysql_tpch_v34_debug_authority_v1",
                    "scenario": "tpch_memory",
                    "bundle_id": bundle_id,
                    "source_bundle_id": source_id,
                    "outer_attempts": 1,
                    "accepted_round_authorized": False,
                    "fresh_in_band_r0_required": True,
                    "historical_v33_r0_role": "readiness_only",
                    "resource_envelope": {
                        **normalized_envelope,
                        "cpuset_cpus": "200-207,456-463",
                        "cpuset_mems": "1",
                        "device": "/dev/nvme2n1",
                    },
                },
            }
        )
        readiness_payload = canonical(readiness)
        readiness_sha = digest_bytes(readiness_payload)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_payload)
        (stage / "closure.json").write_bytes(canonical(closure))

        files = {
            path.name: digest(path)
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        manifest = {
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(OUTPUT_ROOT),
            "files": files,
            "publication": "staged_then_atomic_rename",
        }
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        readonly(stage)
        stage.rename(final)
        try:
            result = verify_readiness(final / readiness_name)
        except BaseException:
            quarantine = (
                BUNDLE_DESTINATION / f".{bundle_id}.failed.{os.getpid()}"
            )
            final.rename(quarantine)
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(
        json.dumps(
            {
                "status": "PUBLISHED_VERIFIED",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "directory": str(final),
                "config": str(final / "config.json"),
                "readiness_manifest": str(final / readiness_name),
                "launcher": str(final / "launch_tpch_v34_debug.py"),
                **result,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create JOB attempt8 with two terminals and proportional resources."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
SOURCE = LANE_ROOT / "bundle" / "attempt7_frozen"
DESTINATION = LANE_ROOT / "bundle" / "attempt8_frozen"
SCORING_SOURCE = LANE_ROOT / "assets" / "common_scoring_contract.json"
SCORING_SHA256 = "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588"
R0_GATE_SOURCE = LANE_ROOT / "assets" / "r0_resource_gate_contract.json"
R0_GATE_SHA256 = "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e"
STABILITY_GATE_SOURCE = LANE_ROOT / "assets" / "stability_gate_contract.json"
STABILITY_GATE_SHA256 = "ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2"
IO_UTILIZATION_SOURCE = LANE_ROOT / "assets" / "io_utilization_contract.json"
IO_UTILIZATION_SHA256 = "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064"
FREEZE_ID = "frozen_formal_config_20260722_job_attempt8_v1"
MEMORY_BYTES = 3_221_225_472  # 3 GiB
IO_DIRECTION_BPS = 134_217_728  # 128 MiB/s in each direction
IO_TOTAL_BPS = 2 * IO_DIRECTION_BPS  # 256 MiB/s total denominator
CPUSET = "12-17,48-53"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(root: Path, name: str):
    return json.loads((root / name).read_text(encoding="utf-8"))


def write_json(root: Path, name: str, value) -> None:
    path = root / name
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise RuntimeError(f"attempt8 frozen directory already exists: {DESTINATION}")
    if not (SOURCE / "FROZEN_READY.json").is_file():
        raise RuntimeError(f"source frozen bundle is incomplete: {SOURCE}")
    if not SCORING_SOURCE.is_file() or sha256_file(SCORING_SOURCE) != SCORING_SHA256:
        raise RuntimeError("public common scoring contract is missing or has the wrong checksum")
    if not R0_GATE_SOURCE.is_file() or sha256_file(R0_GATE_SOURCE) != R0_GATE_SHA256:
        raise RuntimeError("public R0 resource gate contract is missing or has the wrong checksum")
    if (
        not STABILITY_GATE_SOURCE.is_file()
        or sha256_file(STABILITY_GATE_SOURCE) != STABILITY_GATE_SHA256
    ):
        raise RuntimeError("public stability gate contract is missing or has the wrong checksum")
    if (
        not IO_UTILIZATION_SOURCE.is_file()
        or sha256_file(IO_UTILIZATION_SOURCE) != IO_UTILIZATION_SHA256
    ):
        raise RuntimeError("public I/O utilization contract is missing or has the wrong checksum")
    temporary = DESTINATION.with_name(f".{DESTINATION.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"temporary destination already exists: {temporary}")
    shutil.copytree(SOURCE, temporary, symlinks=False)
    shutil.copy2(SCORING_SOURCE, temporary / "common_scoring_contract.json")
    shutil.copy2(R0_GATE_SOURCE, temporary / "r0_resource_gate_contract.json")
    shutil.copy2(STABILITY_GATE_SOURCE, temporary / "stability_gate_contract.json")
    shutil.copy2(IO_UTILIZATION_SOURCE, temporary / "io_utilization_contract.json")

    benchmark = read_json(temporary, "benchmark_matrix.json")
    benchmark["config_id"] = "benchmark_matrix_20260722_job_attempt8_two_terminals"
    benchmark["workloads"]["job"]["clients"] = 2
    benchmark["workloads"]["job"]["engine_terminals"] = {"postgresql": 2}
    benchmark["attempt8_revision"] = {
        "scope": "postgresql JOB attempt8",
        "job_terminals": 2,
        "reason": "increase 30-second completion counts without relaxing the CV gate",
        "attempt6_retroactive_acceptance": False,
    }
    write_json(temporary, "benchmark_matrix.json", benchmark)

    machine = read_json(temporary, "machine_and_resources.json")
    machine["config_id"] = "machine_and_resources_20260722_job_attempt8_two_terminals"
    for profile in (
        machine["resource_profiles"]["memory"],
        machine["engine_resource_profiles"]["postgresql"]["memory"],
    ):
        profile.update(
            {
                "cpu_quota_cores": 3.0,
                "cpuset_cpus": CPUSET,
                "cpuset_mems": "0",
                "memory_bytes": MEMORY_BYTES,
                "memory_swap_bytes": MEMORY_BYTES,
                "io_read_bps": IO_DIRECTION_BPS,
                "io_write_bps": IO_DIRECTION_BPS,
                "pids_limit": 512,
                "network_mode": "bridge",
            }
        )
    machine["attempt8_revision"] = {
        "scope": "postgresql JOB memory baseline and subsequent gated method run",
        "parent_freeze_id": "frozen_formal_config_20260722_job_attempt7_v1",
        "attempt6_retroactive_acceptance": False,
        "cpu_quota_cores": 3.0,
        "memory_cap_gib": 3.0,
        "io_read_cap_mib_per_second": 128,
        "io_write_cap_mib_per_second": 128,
        "io_total_cap_mib_per_second": 256,
        "io_utilization_definition": (
            "(read_bps+write_bps)/(io_read_bps+io_write_bps)"
        ),
        "container_swap": "disabled_by_memory_swap_equal_memory",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
    }
    write_json(temporary, "machine_and_resources.json", machine)

    fixed = read_json(temporary, "fixed_db_settings.json")
    fixed["config_id"] = "fixed_db_settings_20260722_job_attempt8_two_terminals"
    fixed["postgresql"]["memory"]["shared_buffers"] = "2GB"
    fixed["postgresql"]["memory"]["effective_cache_size"] = "3GB"
    fixed["attempt8_revision"] = {
        "shared_buffers_max_gib": 2,
        "memory_cap_gib": 3.0,
        "nominal_non_shared_headroom_gib": 1.0,
        "effective_cache_size_is_planner_hint": True,
        "readback_required": True,
    }
    write_json(temporary, "fixed_db_settings.json", fixed)

    measurement = read_json(temporary, "measurement_protocol.json")
    measurement["config_id"] = "measurement_protocol_20260722_v16_job_resource_only_probe300"
    measurement["timing"]["initial_probe_seconds"] = 300
    measurement["timing"]["continue_after_probe_when_unstable"] = True
    measurement["stability"]["calculation"] = (
        "coefficient_of_variation_across_six_window_means"
    )
    measurement["stability"]["normal_throughput_calculation"] = "outer_acceptance_only"
    measurement["stability"]["cpu_cv_max"] = 0.10
    measurement["stability"]["memory_cv_max"] = 0.10
    measurement["stability"]["io_cv_max"] = 0.10
    measurement["stability"]["normal_throughput_cv_max"] = 0.10
    measurement["stability"]["normal_throughput_window_aggregation"] = "arithmetic_mean"
    measurement["stability"]["resource_window_aggregation"] = "arithmetic_mean"
    measurement["stability"]["recent_complete_window_count"] = 6
    measurement["stability"].pop("all_four_metrics_must_pass", None)
    measurement["stability"]["all_cpu_memory_io_resources_must_be_stable"] = True
    measurement["stability"]["throughput_fields_required_for_stability"] = False
    measurement["stability"]["throughput_role"] = "outer_acceptance_only"
    measurement["stability"]["within_window_one_second_cv_required"] = False
    measurement["stability"]["raw_one_second_samples_retained"] = True
    measurement["resource_utilization_definitions"] = {
        **measurement.get("resource_utilization_definitions", {}),
        "io": {
            "contract_id": "pg5_io_total_throughput_utilization_v1",
            "contract_file": "io_utilization_contract.json",
            "contract_sha256": IO_UTILIZATION_SHA256,
            "formula": "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)",
            "numerator": "read_bps + write_bps",
            "denominator": "read_cap_bps + write_cap_bps",
            "read_cap_bps": IO_DIRECTION_BPS,
            "write_cap_bps": IO_DIRECTION_BPS,
            "io_total_cap_bps": IO_TOTAL_BPS,
            "single_channel_max_formula_allowed": False,
        },
    }
    measurement["baseline_gates"].pop("target_resource_utilization_min", None)
    measurement["baseline_gates"].pop("target_resource_utilization_max", None)
    measurement["baseline_gates"]["target_resource"] = "memory"
    measurement["baseline_gates"]["target_rule"] = "strictly_greater_than"
    measurement["baseline_gates"]["target_resource_utilization_min_exclusive"] = 0.80
    measurement["baseline_gates"]["target_resource_utilization_max"] = None
    measurement["baseline_gates"]["non_target_resource_utilization_min"] = 0.30
    measurement["baseline_gates"]["non_target_resource_utilization_max"] = 0.50
    measurement["attempt8_revision"] = {
        "scope": "JOB attempt8 formal R0 and later points launched from this freeze only",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
        "attempt4_retroactive_acceptance": False,
        "attempt5_retroactive_acceptance": False,
        "attempt6_retroactive_acceptance": False,
        "raw_utilization_validation": "finite_and_non_negative_no_upper_bound",
        "raw_utilization_persistence": "original_value_without_clipping",
        "common_scoring_contract_file": "common_scoring_contract.json",
        "common_scoring_contract_sha256": SCORING_SHA256,
        "r0_resource_gate_contract_file": "r0_resource_gate_contract.json",
        "r0_resource_gate_contract_sha256": R0_GATE_SHA256,
        "stability_gate_contract_file": "stability_gate_contract.json",
        "stability_gate_contract_sha256": STABILITY_GATE_SHA256,
        "io_utilization_contract_file": "io_utilization_contract.json",
        "io_utilization_contract_sha256": IO_UTILIZATION_SHA256,
        "resource_only_cross_window_rules": {
            "metrics": ["cpu_utilization", "memory_utilization", "io_utilization"],
            "throughput_fields_required_for_stability": False,
            "throughput_role": "outer_acceptance_only",
            "window_seconds": 30,
            "consecutive_complete_windows": 6,
            "cross_window_cv_max": 0.10,
            "within_window_one_second_cv_required": False,
            "sample_coverage_min": 0.9,
            "aggregate_sample_coverage_min": 0.9,
            "minimum_measurement_seconds": 180,
            "initial_probe_seconds": 300,
            "continue_after_probe_when_unstable": True,
            "maximum_measurement_seconds": 600,
        },
    }
    write_json(temporary, "measurement_protocol.json", measurement)

    preflight = read_json(temporary, "preflight_gates.json")
    preflight["config_id"] = "preflight_gates_20260722_job_attempt8_authoritative_gate"
    preflight["baseline_bottleneck_gate"].pop("target_utilization_min", None)
    preflight["baseline_bottleneck_gate"].pop("target_utilization_max", None)
    preflight["baseline_bottleneck_gate"]["target_resource"] = "memory"
    preflight["baseline_bottleneck_gate"]["target_rule"] = "strictly_greater_than"
    preflight["baseline_bottleneck_gate"]["target_utilization_min_exclusive"] = 0.80
    preflight["baseline_bottleneck_gate"]["target_utilization_max"] = None
    preflight["baseline_bottleneck_gate"]["non_target_utilization_min"] = 0.30
    preflight["baseline_bottleneck_gate"]["non_target_utilization_max"] = 0.50
    preflight["attempt8_revision"] = {
        "scope": "JOB attempt8",
        "cpu_and_io_range": [0.30, 0.50],
        "memory_target_rule": "strictly_greater_than_0.80_no_upper_bound",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
        "attempt4_retroactive_acceptance": False,
        "attempt5_retroactive_acceptance": False,
        "attempt6_retroactive_acceptance": False,
    }
    write_json(temporary, "preflight_gates.json", preflight)

    contract = read_json(temporary, "freeze_contract.json")
    contract["freeze_id"] = FREEZE_ID
    contract["bundle_root"] = str(DESTINATION)
    if "common_scoring_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("common_scoring_contract.json")
    if "r0_resource_gate_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("r0_resource_gate_contract.json")
    if "stability_gate_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("stability_gate_contract.json")
    if "io_utilization_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("io_utilization_contract.json")
    contract["versioning"]["parent_freeze_id"] = (
        "frozen_formal_config_20260722_job_attempt7_v1"
    )
    contract["versioning"]["authorized_scope"] = "JOB attempt8 only"
    write_json(temporary, "freeze_contract.json", contract)

    readme = (temporary / "FROZEN_CONFIG.md").read_text(encoding="utf-8")
    preface = (
        "# JOB attempt8 resource-only \n\n"
        f"freeze_id=`{FREEZE_ID}` JOB attempt8  R0 "
        "Configurationattempt1/2/3/4/5/6 JOB terminals=2"
        "CPU=3IO read/write128MiB/scap=256MiB/sCPU/IO"
        "R0CPU/Memory/IO"
        ">=0>1clipMemory=3 GiBno swap"
        "CPU=3I/O read=128 MiB/swrite=128 MiB/stotal=256 MiB/s"
        "pids=512shared_buffers=2GB"
        "effective_cache_size=3GBCPU/Memory/IO30"
        "6 CV<=10%QPSouter acceptanceIO(read_bps+write_bps)/"
        "(read_cap+write_cap)max coverage"
        ">=90%1CVminimum=180sinitial probe=300smax=600sfail closedR0 Memory "
        ">80%CPU/IO30%–50%scoring"
        f"`common_scoring_contract.json`SHA-256=`{SCORING_SHA256}`R0"
        f"SHA-256=`{R0_GATE_SHA256}`SHA-256=`{STABILITY_GATE_SHA256}`"
        f"IOSHA-256=`{IO_UTILIZATION_SHA256}`\n\n"
    )
    (temporary / "FROZEN_CONFIG.md").write_text(preface + readme, encoding="utf-8")

    payload_names = sorted(
        path.name
        for path in temporary.iterdir()
        if path.is_file() and path.name not in {"checksums.sha256", "FROZEN_READY.json"}
    )
    checksum_lines = [
        f"{sha256_file(temporary / name)}  {name}" for name in payload_names
    ]
    (temporary / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    ready = read_json(temporary, "FROZEN_READY.json")
    ready.update(
        {
            "freeze_id": FREEZE_ID,
            "published_at_utc": dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "payload_file_count": len(payload_names),
            "checksums_sha256": sha256_file(temporary / "checksums.sha256"),
            "parent_freeze_id": "frozen_formal_config_20260722_job_attempt7_v1",
            "scope": "postgresql JOB attempt8",
            "attempt1_retroactive_acceptance": False,
            "attempt2_retroactive_acceptance": False,
            "attempt3_retroactive_acceptance": False,
            "attempt4_retroactive_acceptance": False,
            "attempt5_retroactive_acceptance": False,
            "attempt6_retroactive_acceptance": False,
            "common_scoring_contract_sha256": SCORING_SHA256,
            "r0_resource_gate_contract_sha256": R0_GATE_SHA256,
            "stability_gate_contract_sha256": STABILITY_GATE_SHA256,
            "io_utilization_contract_sha256": IO_UTILIZATION_SHA256,
        }
    )
    write_json(temporary, "FROZEN_READY.json", ready)
    os.replace(temporary, DESTINATION)
    print(
        json.dumps(
            {
                "status": "ready",
                "freeze_id": FREEZE_ID,
                "path": str(DESTINATION),
                "checksums_sha256": sha256_file(DESTINATION / "checksums.sha256"),
                "payload_file_count": len(payload_names),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

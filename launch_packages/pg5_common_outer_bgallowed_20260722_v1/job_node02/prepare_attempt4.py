#!/usr/bin/env python3
"""Create JOB attempt4 with the authoritative bottleneck/scoring contract."""

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
SOURCE = LANE_ROOT / "bundle" / "attempt3_frozen"
DESTINATION = LANE_ROOT / "bundle" / "attempt4_frozen"
SCORING_SOURCE = LANE_ROOT / "assets" / "common_scoring_contract.json"
SCORING_SHA256 = "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588"
R0_GATE_SOURCE = LANE_ROOT / "assets" / "r0_resource_gate_contract.json"
R0_GATE_SHA256 = "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e"
FREEZE_ID = "frozen_formal_config_20260722_job_attempt4_v1"
MEMORY_BYTES = 3_221_225_472  # 3 GiB
IO_BPS = 268_435_456  # 256 MiB/s
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
        raise RuntimeError(f"attempt4 frozen directory already exists: {DESTINATION}")
    if not (SOURCE / "FROZEN_READY.json").is_file():
        raise RuntimeError(f"source frozen bundle is incomplete: {SOURCE}")
    if not SCORING_SOURCE.is_file() or sha256_file(SCORING_SOURCE) != SCORING_SHA256:
        raise RuntimeError("public common scoring contract is missing or has the wrong checksum")
    if not R0_GATE_SOURCE.is_file() or sha256_file(R0_GATE_SOURCE) != R0_GATE_SHA256:
        raise RuntimeError("public R0 resource gate contract is missing or has the wrong checksum")
    temporary = DESTINATION.with_name(f".{DESTINATION.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"temporary destination already exists: {temporary}")
    shutil.copytree(SOURCE, temporary, symlinks=False)
    shutil.copy2(SCORING_SOURCE, temporary / "common_scoring_contract.json")
    shutil.copy2(R0_GATE_SOURCE, temporary / "r0_resource_gate_contract.json")

    machine = read_json(temporary, "machine_and_resources.json")
    machine["config_id"] = "machine_and_resources_20260722_job_attempt4_final_gate"
    for profile in (
        machine["resource_profiles"]["memory"],
        machine["engine_resource_profiles"]["postgresql"]["memory"],
    ):
        profile.update(
            {
                "cpu_quota_cores": 1.5,
                "cpuset_cpus": CPUSET,
                "cpuset_mems": "0",
                "memory_bytes": MEMORY_BYTES,
                "memory_swap_bytes": MEMORY_BYTES,
                "io_read_bps": IO_BPS,
                "io_write_bps": IO_BPS,
                "pids_limit": 512,
                "network_mode": "bridge",
            }
        )
    machine["parent_attempt3_revision"] = machine.pop("attempt3_revision", None)
    machine["attempt4_revision"] = {
        "scope": "postgresql JOB memory baseline and subsequent gated method run",
        "parent_freeze_id": "frozen_formal_config_20260722_job_attempt3_v1",
        "derived_from_attempt3_raw_samples": True,
        "cpu_quota_cores": 1.5,
        "memory_cap_gib": 3.0,
        "io_cap_mib_per_second": 256,
        "container_swap": "disabled_by_memory_swap_equal_memory",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
    }
    write_json(temporary, "machine_and_resources.json", machine)

    fixed = read_json(temporary, "fixed_db_settings.json")
    fixed["config_id"] = "fixed_db_settings_20260722_job_attempt4_oom_headroom"
    fixed["postgresql"]["memory"]["shared_buffers"] = "2GB"
    fixed["postgresql"]["memory"]["effective_cache_size"] = "3GB"
    fixed["parent_attempt3_revision"] = fixed.pop("attempt3_revision", None)
    fixed["attempt4_revision"] = {
        "shared_buffers_max_gib": 2,
        "memory_cap_gib": 3.0,
        "nominal_non_shared_headroom_gib": 1.0,
        "effective_cache_size_is_planner_hint": True,
        "readback_required": True,
    }
    write_json(temporary, "fixed_db_settings.json", fixed)

    measurement = read_json(temporary, "measurement_protocol.json")
    measurement["config_id"] = "measurement_protocol_20260722_v12_job_authoritative_gate"
    measurement["stability"]["calculation"] = (
        "resources_within_each_window_and_qps_cv_across_latest_six_window_means"
    )
    measurement["stability"]["normal_throughput_calculation"] = (
        "per_window_mean_then_cv_across_latest_six_windows"
    )
    measurement["stability"]["normal_throughput_window_aggregation"] = "arithmetic_mean"
    measurement["stability"]["normal_throughput_recent_window_count"] = 6
    measurement["stability"]["raw_one_second_samples_retained"] = True
    measurement["baseline_gates"].pop("target_resource_utilization_min", None)
    measurement["baseline_gates"].pop("target_resource_utilization_max", None)
    measurement["baseline_gates"]["target_resource"] = "memory"
    measurement["baseline_gates"]["target_rule"] = "strictly_greater_than"
    measurement["baseline_gates"]["target_resource_utilization_min_exclusive"] = 0.80
    measurement["baseline_gates"]["target_resource_utilization_max"] = None
    measurement["baseline_gates"]["non_target_resource_utilization_min"] = 0.30
    measurement["baseline_gates"]["non_target_resource_utilization_max"] = 0.50
    measurement["parent_attempt3_revision"] = measurement.pop("attempt3_revision", None)
    measurement["attempt4_revision"] = {
        "scope": "JOB attempt4 and later points launched from this freeze only",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
        "common_scoring_contract_file": "common_scoring_contract.json",
        "common_scoring_contract_sha256": SCORING_SHA256,
        "r0_resource_gate_contract_file": "r0_resource_gate_contract.json",
        "r0_resource_gate_contract_sha256": R0_GATE_SHA256,
        "resource_window_rules_unchanged": {
            "cpu_cv_max": 0.05,
            "memory_cv_max": 0.03,
            "io_cv_max": 0.05,
            "sample_coverage_min": 0.9,
        },
    }
    write_json(temporary, "measurement_protocol.json", measurement)

    preflight = read_json(temporary, "preflight_gates.json")
    preflight["config_id"] = "preflight_gates_20260722_job_attempt4_authoritative_gate"
    preflight["baseline_bottleneck_gate"].pop("target_utilization_min", None)
    preflight["baseline_bottleneck_gate"].pop("target_utilization_max", None)
    preflight["baseline_bottleneck_gate"]["target_resource"] = "memory"
    preflight["baseline_bottleneck_gate"]["target_rule"] = "strictly_greater_than"
    preflight["baseline_bottleneck_gate"]["target_utilization_min_exclusive"] = 0.80
    preflight["baseline_bottleneck_gate"]["target_utilization_max"] = None
    preflight["baseline_bottleneck_gate"]["non_target_utilization_min"] = 0.30
    preflight["baseline_bottleneck_gate"]["non_target_utilization_max"] = 0.50
    preflight["parent_attempt3_revision"] = preflight.pop("attempt3_revision", None)
    preflight["attempt4_revision"] = {
        "scope": "JOB attempt4",
        "cpu_and_io_range": [0.30, 0.50],
        "memory_target_rule": "strictly_greater_than_0.80_no_upper_bound",
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
    }
    write_json(temporary, "preflight_gates.json", preflight)

    contract = read_json(temporary, "freeze_contract.json")
    contract["freeze_id"] = FREEZE_ID
    contract["bundle_root"] = str(DESTINATION)
    if "common_scoring_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("common_scoring_contract.json")
    if "r0_resource_gate_contract.json" not in contract["immutable_files"]:
        contract["immutable_files"].append("r0_resource_gate_contract.json")
    contract["versioning"]["parent_freeze_id"] = (
        "frozen_formal_config_20260722_job_attempt3_v1"
    )
    contract["versioning"]["authorized_scope"] = "JOB attempt4 only"
    write_json(temporary, "freeze_contract.json", contract)

    readme = (temporary / "FROZEN_CONFIG.md").read_text(encoding="utf-8")
    preface = (
        "# JOB attempt4 /\n\n"
        f"freeze_id=`{FREEZE_ID}` JOB attempt4  R0 "
        "Configurationattempt1/2/3 Memory=3 GiBno swap"
        "CPU=1.5I/O=256 MiB/spids=512shared_buffers=2GB"
        "effective_cache_size=3GB301QPS"
        "306 CV<=5%R0 Memory "
        ">80%CPU/IO30%–50%scoring"
        f"`common_scoring_contract.json`SHA-256=`{SCORING_SHA256}`R0"
        f"SHA-256=`{R0_GATE_SHA256}`\n\n"
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
            "parent_freeze_id": "frozen_formal_config_20260722_job_attempt3_v1",
            "scope": "postgresql JOB attempt4",
            "attempt1_retroactive_acceptance": False,
            "attempt2_retroactive_acceptance": False,
            "attempt3_retroactive_acceptance": False,
            "common_scoring_contract_sha256": SCORING_SHA256,
            "r0_resource_gate_contract_sha256": R0_GATE_SHA256,
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

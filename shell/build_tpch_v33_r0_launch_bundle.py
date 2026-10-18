#!/usr/bin/env python3
"""Publish the closure-complete deterministic TPC-H v33 fresh-R0 bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
BASE_CONFIG = READINESS / "tpch_v29_config_cpu190_io320_parallel4_affinity_v1.json"
BASE_PROFILE = READINESS / "tpch_v29_profile_cpu190_io320_parallel4_affinity_v1.json"
SOURCE = (
    READINESS
    / "source-v33-c1b919b73747ef3eddbae9ed8f0427cbd7a954a18c0b88bd26db6a9b038e7e09"
)
DESTINATION = ROOT / "artifacts/mysql_tpch_v33_r0"
OUTPUT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v33_cpu450_io576_terminal4_deterministic_v1"
)


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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def verify(directory: Path, files: dict[str, str]) -> None:
    manifest = json.loads(
        (directory / "launch_manifest.json").read_text(encoding="utf-8")
    )
    if directory.name != manifest.get("bundle_id") or manifest.get("files") != files:
        raise RuntimeError("existing TPC-H v33 launch identity differs")
    for relative, expected in files.items():
        path = directory / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v33 launch content differs: {relative}")


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"TPC-H v33 fresh output already exists: {OUTPUT}")
    source_manifest = json.loads(
        (SOURCE / "source_manifest.json").read_text(encoding="utf-8")
    )
    if (
        source_manifest.get("bundle_id") != SOURCE.name.removeprefix("source-v33-")
        or source_manifest.get("schema_version")
        != "mysql_tpch_local_deterministic_source_v33"
    ):
        raise RuntimeError("TPC-H v33 source authority differs")

    profile = json.loads(BASE_PROFILE.read_text(encoding="utf-8"))
    profile.update(
        {
            "cpu_cores": 4.5,
            "read_bps": 603979776,
            "write_bps": 603979776,
            "derivation": {
                "schema_version": "mysql_tpch_v33_demand_recalibration_v1",
                "reason":
                    "v29/v30 declared four terminals but executed two; "
                    "Phase transaction selection was unseeded",
                "v29_observed": {
                    "terminals_actual": 2,
                    "completed": 19,
                    "cpu_ratio": 0.4526820567303565,
                    "io_ratio": 0.36315472425728523,
                },
                "v30_observed": {
                    "terminals_actual": 2,
                    "completed": 1,
                    "cpu_ratio": 0.9001393236260109,
                    "io_ratio": 0.03763674745581266,
                },
                "v29_v30_resource_envelope_identical": True,
                "prior_results_invalid_for_four_terminal_demand": True,
                "new_terminals_actual": 4,
                "new_cpu_cores": 4.5,
                "cpu_method":
                    "midpoint extrapolation of 0.860 and 1.710 observed "
                    "CPU-core demand to four deterministic terminals with margin",
                "new_io_bps_per_direction": 603979776,
                "io_method":
                    "double v29 aggregate throughput then size the symmetric "
                    "denominator for approximately 0.40 with headroom",
                "failed_v30_not_reused_as_r0": True,
            },
        }
    )
    config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    config.update(
        {
            "exp_id":
                "mysql_algorithm_c.tpch_memory.r0_v33_deterministic_terminal4",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "fresh_r0_only": True,
            "outer_execution_authorized": False,
            "accepted_round_authorized": False,
            "profile": "__BUNDLE_PROFILE__",
            "measurement_seconds": 600,
            "minimum_measurement_seconds": 180,
            "minimum_client_completions": 4,
        }
    )
    config["output"]["directory"] = str(OUTPUT)
    config["workload"].update(
        {
            "tpch_terminals": 4,
            "host_control_cpuset": "200-207,456-463",
            "host_affinity_generation":
                "v33_exact_controller_and_workload",
            "stability_rationale":
                "deterministic four-terminal demand recalibration; "
                "v29/v30 two-terminal measurements not reused",
            "schedule_seed": 4508333,
            "phase_rng_seeded": True,
            "phase_selection_synchronized": True,
            "v19_resource_contract": {
                "cpu_quota_cores": 4.5,
                "io_read_bps": 603979776,
                "io_read_mib_per_s": 576,
                "io_write_bps": 603979776,
                "io_write_mib_per_s": 576,
                "terminals": 4,
            },
            "v33_resource_contract": {
                "cpu_quota_cores": 4.5,
                "io_read_bps": 603979776,
                "io_write_bps": 603979776,
                "terminals": 4,
            },
        }
    )
    config["early_stop_source_bundle"]["source"] = str(SOURCE)
    config["early_stop_source_bundle"]["source_manifest_sha256"] = sha256(
        SOURCE / "source_manifest.json"
    )

    payloads = {
        "config.json": canonical(config),
        "profile.json": canonical(profile),
        "tpch_v33_r0_driver.py": (
            ROOT / "shell/tpch_v33_r0_driver.py"
        ).read_bytes(),
        "mysql_numa_bind_exec_v1.py": (
            ROOT / "shell/mysql_numa_bind_exec_v1.py"
        ).read_bytes(),
    }
    files = {
        relative: hashlib.sha256(payload).hexdigest()
        for relative, payload in sorted(payloads.items())
    }
    identity = {
        "schema_version": "mysql_tpch_v33_r0_launch_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "fresh_r0_authorized": True,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
        "source_bundle": {
            "directory": str(SOURCE),
            "bundle_id": source_manifest["bundle_id"],
            "source_manifest_sha256": sha256(SOURCE / "source_manifest.json"),
        },
        "resource_envelope": {
            "cpu_quota_cores": 4.5,
            "memory_bytes": 5583454208,
            "memory_swap_bytes": 0,
            "io_read_bps": 603979776,
            "io_write_bps": 603979776,
            "pids_max": 512,
            "cpuset_cpus": "200-207,456-463",
            "cpuset_mems": "1",
            "device": "/dev/nvme2n1",
            "port": 65203,
        },
        "workload": {
            "terminals": 4,
            "random_seed": 4508333,
            "deterministic_global_selection": True,
            "minimum_client_completions": 4,
        },
        "preparation": {
            "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
            "timeout_seconds": 1200,
            "measurement_timeout_unchanged": True,
        },
        "output": str(OUTPUT),
        "files": files,
    }
    bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
    final = DESTINATION / bundle_id
    if final.exists():
        verify(final, files)
        print(json.dumps({"status": "EXISTS_VERIFIED", "directory": str(final)}))
        return 0
    DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = DESTINATION / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            path = stage / relative
            path.write_bytes(payload)
        manifest = {**identity, "bundle_id": bundle_id}
        (stage / "launch_manifest.json").write_bytes(canonical(manifest))
        (stage / "launch_manifest.json.sha256").write_text(
            f"{sha256(stage / 'launch_manifest.json')}  launch_manifest.json\n",
            encoding="ascii",
        )
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    verify(final, files)
    print(
        json.dumps(
            {
                "status": "PUBLISHED_VERIFIED",
                "bundle_id": bundle_id,
                "directory": str(final),
                "source_bundle_id": source_manifest["bundle_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

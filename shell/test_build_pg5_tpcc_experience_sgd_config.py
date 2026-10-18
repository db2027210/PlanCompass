#!/usr/bin/env python3
"""Regression tests for fail-closed TPC-C capability-profile binding."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from build_pg5_tpcc_experience_sgd_config import (
    CapabilityProfile,
    build_direction_ranker_contract,
    build_workload_command,
    load_manifest_profile,
    parse_args,
    require_matching_profile,
)


def write_json(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def expect_value_error(func: object, *args: object, **kwargs: object) -> None:
    try:
        func(*args, **kwargs)  # type: ignore[operator]
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def main() -> None:
    profile = CapabilityProfile(
        cpu_cores=3.85,
        memory_limit_bytes=4 * 1024 ** 3,
        io_total_limit_mib_s=54,
        io_read_cap_mib_s=27,
        io_write_cap_mib_s=27,
    )
    assert profile.capability() == {"cpu": 3.85, "memory": 4.0, "io": 54}
    assert profile.io_bps(27) == 28_311_552

    with tempfile.TemporaryDirectory(prefix="tpcc-config-profile-") as temp_dir:
        root = Path(temp_dir)
        manifest = write_json(
            root,
            "manifest.json",
            {
                "capability_profile": {
                    "cpu": 3.85,
                    "memory": 4,
                    "io": 54,
                    "memory_limit_bytes": 4 * 1024 ** 3,
                    "io_read_cap_mib_s": 27,
                    "io_write_cap_mib_s": 27,
                }
            },
        )
        ranker = write_json(
            root,
            "ranker.json",
            {
                "training_contract": {
                    "capability_profiles": [{"cpu": 3.85, "memory": 4, "io": 54}]
                }
            },
        )
        require_matching_profile(
            profile,
            load_manifest_profile(
                manifest,
                source="manifest",
                require_physical_limits=True,
            ),
            source="manifest",
        )
        require_matching_profile(
            profile,
            load_manifest_profile(ranker, source="ranker"),
            source="ranker",
        )
        contract = build_direction_ranker_contract(ranker, profile)
        assert contract["required"] is True
        assert contract["enabled"] is True
        assert contract["expected_capability_profile"] == {
            "cpu": 3.85,
            "memory": 4.0,
            "io": 54,
        }
        assert contract["policy"]["block_estimated_ranking_when_not_ready"] is True
        assert contract["policy"]["block_real_replay_when_not_ready"] is True

        workload_command = build_workload_command(
            Path("/runner.py"),
            Path("/measurements"),
            profile,
        )
        assert "--cpu-limit-cores 3.85" in workload_command
        assert "--memory-limit-bytes 4294967296" in workload_command
        assert "--io-total-limit-mib-s 54" in workload_command
        assert "--io-read-cap-mib-s 27" in workload_command
        assert "--io-write-cap-mib-s 27" in workload_command
        assert "5368709120" not in workload_command

        mismatched = write_json(
            root,
            "mismatched.json",
            {
                "capability_profile": {
                    "cpu": 3.85,
                    "memory": 4,
                    "io": 54,
                    "memory_limit_bytes": 4 * 1024 ** 3 + 1,
                    "io_read_cap_mib_s": 27,
                    "io_write_cap_mib_s": 27,
                }
            },
        )
        expect_value_error(
            require_matching_profile,
            profile,
            load_manifest_profile(
                mismatched,
                source="manifest",
                require_physical_limits=True,
            ),
            source="manifest",
        )
        missing = write_json(root, "missing.json", {"capability_profile": {"cpu": 3.85}})
        expect_value_error(
            load_manifest_profile,
            missing,
            source="manifest",
            require_physical_limits=True,
        )
        multiple = write_json(
            root,
            "multiple.json",
            {
                "training_contract": {
                    "capability_profiles": [
                        {"cpu": 3.85, "memory": 4, "io": 54},
                        {"cpu": 3.85, "memory": 5, "io": 54},
                    ]
                }
            },
        )
        expect_value_error(load_manifest_profile, multiple, source="ranker")

        args = parse_args(
            [
                "--capability-manifest", str(manifest),
                "--direction-ranker", str(ranker),
                "--cpu-cap-cores", "3.85",
                "--memory-limit-bytes", str(4 * 1024 ** 3),
                "--io-total-limit-mib-s", "54",
                "--io-read-cap-mib-s", "27",
                "--io-write-cap-mib-s", "27",
            ]
        )
        assert args.memory_limit_bytes == 4 * 1024 ** 3

    expect_value_error(
        CapabilityProfile,
        cpu_cores=3.85,
        memory_limit_bytes=4 * 1024 ** 3,
        io_total_limit_mib_s=54,
        io_read_cap_mib_s=20,
        io_write_cap_mib_s=20,
    )
    print("TPC-C config capability-profile binding regression passed", flush=True)


if __name__ == "__main__":
    main()

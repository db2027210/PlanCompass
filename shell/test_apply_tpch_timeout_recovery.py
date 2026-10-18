#!/usr/bin/env python3
"""Focused tests for the audited TPC-H hard-timeout helper."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from apply_tpch_timeout_recovery import main


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_recovery_enforces_only_preformal_tpch_config() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root = root / "run"
        scene_dir = run_root / "tpch_memory"
        write_json(
            scene_dir / "optimizer_config.json",
            {
                "algorithm_c": {
                    "scene": "tpch_memory",
                    "workload_contract": {
                        "benchmark_concurrency": {
                            "target_wall_time_s": 900,
                            "target_wall_time_is_advisory": True,
                        }
                    },
                },
                "binary_plan_tuning": {
                    "validation": {
                        "hybench_xp": {
                            "timeout_s": 1800,
                            "timeout_grace_s": 1800,
                        }
                    }
                },
            },
        )
        write_json(run_root / "launch_manifest.json", {"scenes": {}})
        write_json(
            scene_dir / "workload_contract.json",
            {
                "benchmark_concurrency": {
                    "target_wall_time_s": 900,
                    "target_wall_time_is_advisory": True,
                }
            },
        )

        argv = ["apply_tpch_timeout_recovery.py", str(run_root)]
        with mock.patch.object(sys, "argv", argv):
            assert main() == 0

        config = json.loads(
            (scene_dir / "optimizer_config.json").read_text(encoding="utf-8")
        )
        hybench = config["binary_plan_tuning"]["validation"]["hybench_xp"]
        assert hybench["timeout_s"] == 1020
        assert hybench["timeout_grace_s"] == 120
        assert (
            config["algorithm_c"]["workload_contract"][
                "benchmark_concurrency"
            ]["target_wall_time_is_advisory"]
            is False
        )
        enforcement = config["algorithm_c"][
            "preformal_runtime_timeout_enforcement"
        ]
        assert enforcement["previous"]["timeout_s"] == 1800
        assert enforcement["previous_target_wall_time_is_advisory"] is True
        assert enforcement["current"]["timeout_s"] == 1020
        standalone = json.loads(
            (scene_dir / "workload_contract.json").read_text(encoding="utf-8")
        )
        assert (
            standalone["benchmark_concurrency"][
                "target_wall_time_is_advisory"
            ]
            is False
        )

        manifest = json.loads(
            (run_root / "launch_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["tpch_timeout_enforcement"]["current"]["timeout_s"] == 1020


def test_recovery_rejects_expanded_or_formal_timeout() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root = root / "run"
        scene_dir = run_root / "tpch_memory"
        write_json(scene_dir / "optimizer_config.json", {})
        write_json(run_root / "launch_manifest.json", {})
        argv = [
            "apply_tpch_timeout_recovery.py",
            str(run_root),
            "--timeout-s",
            "1800",
        ]
        with mock.patch.object(sys, "argv", argv):
            try:
                main()
            except ValueError as exc:
                assert "(900, 1020]" in str(exc)
            else:
                raise AssertionError("expanded TPC-H timeout must be rejected")


def main_test() -> int:
    test_recovery_enforces_only_preformal_tpch_config()
    test_recovery_rejects_expanded_or_formal_timeout()
    print("apply_tpch_timeout_recovery: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_test())

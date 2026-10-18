#!/usr/bin/env python3
"""Regression checks for immutable HyBench state restore in Bonect configs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import run_pg_benchmark_bottleneck_matrix as runner


def _parse_args(extra: list[str] | None = None):
    old_argv = sys.argv
    try:
        sys.argv = [
            "run_pg_benchmark_bottleneck_matrix.py",
            "--scenario-set",
            "bonect",
            "--bonect-bottlenecks",
            "none",
            "--run-id",
            "snapshot_restore_config_test",
            "--use-resource-estimator",
        ] + list(extra or [])
        return runner.parse_args()
    finally:
        sys.argv = old_argv


def _build_config(extra: list[str] | None = None) -> dict:
    args = _parse_args(extra)
    run_id = (
        "snapshot_restore_config_test_disabled"
        if extra and "--no-hybench-snapshot-restore" in extra
        else "snapshot_restore_config_test_enabled"
    )
    spec = runner.CommandSpec(
        benchmark="hybench_xp",
        bottleneck="none",
        command=[],
        cwd=Path(runner.PROJECT_ROOT),
        env={},
        log_path=Path("/tmp/snapshot_restore_config_test.log"),
        pg_dbname="hybench_sf10x",
    )
    config_path = runner.build_optimizer_config(
        spec,
        args,
        run_id,
    )
    return json.loads(config_path.read_text(encoding="utf-8"))


def test_restore_enabled_by_default() -> None:
    cfg = _build_config()
    restore = cfg["pre_benchmark_recovery"]["snapshot_restore"]
    assert restore["enabled"] is True, restore
    assert restore["required"] is True, restore
    assert restore["only_for_tp_workloads"] is False, restore
    assert restore["tags"] == ["baseline", "final", "feedback"], restore
    assert restore["template_db"] == "hybench_sf10x_base", restore
    assert "restore_hybench_sf10x_from_template.sh" in restore["command"], restore
    assert "OPTIMIZER_CONFIG_PATH=" in restore["command"], restore
    assert "VM_RESOURCE_MAX_CPU=96" in restore["command"], restore
    assert "VM_RESOURCE_MAX_MEMORY_GB=64" in restore["command"], restore


def test_explicit_diagnostic_disable() -> None:
    cfg = _build_config(["--no-hybench-snapshot-restore"])
    restore = cfg["pre_benchmark_recovery"]["snapshot_restore"]
    assert restore["enabled"] is False, restore
    assert restore["required"] is False, restore


def test_baseline_only_config() -> None:
    cfg = _build_config(["--baseline-only"])
    assert cfg["benchmark"]["baseline_only"] is True, cfg["benchmark"]


def main() -> None:
    test_restore_enabled_by_default()
    test_explicit_diagnostic_disable()
    test_baseline_only_config()
    print("PASS Bonect HyBench snapshot restore config tests", flush=True)


if __name__ == "__main__":
    main()

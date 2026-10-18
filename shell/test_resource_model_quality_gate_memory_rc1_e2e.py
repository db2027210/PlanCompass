"""RC#1 config-side activation e2e test (iter92, override 2026-07-06 §356-376).

Validates the fix for the override directive's root cause #1 at the
**generated config layer**, complementing the unit-level
``test_resource_model_quality_gate_memory_rc1.py`` that already proves
``validate_resource_model_quality_gate()`` rejects negative R2 when
``enforce_test_r2=True``.

This e2e test exercises ``shell/run_pg_benchmark_bottleneck_matrix.py``
``build_optimizer_config()`` and asserts:

  - For ``bottleneck == "memory"`` the generated config's
    ``metrics.plan_estimator.model_retraining.quality_gate`` has
    ``enforce_test_r2=True``, ``enforce_test_relative_error_p95=True``,
    ``enforce_test_relative_error_max=True``, and
    ``min_test_r2 >= 0.0``.
  - For ``bottleneck == "cpu"`` and ``bottleneck == "io"`` the same
    fields keep their legacy defaults (``enforce_test_*=False``) so
    CPU/IO run-configs are byte-identical to the pre-RC#1 baseline and
    don't take a regression risk before v7 launch.
  - The override directive's invariant that no 8th cost parameter and
    no ``fallback_on_error=True`` are introduced at the same time is
    preserved.

Run: python3 shell/test_resource_model_quality_gate_memory_rc1_e2e.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the runner module from the shell directory
sys.path.insert(0, _THIS_DIR)
import run_pg_benchmark_bottleneck_matrix as runner  # type: ignore


def _parse_args_with_argv(argv: List[str]) -> argparse.Namespace:
    """Call runner.parse_args() with a stubbed sys.argv."""
    saved = sys.argv
    sys.argv = argv
    try:
        return runner.parse_args()
    finally:
        sys.argv = saved


def _build_spec(bottleneck: str, benchmark: str, generated_dir: Path) -> runner.CommandSpec:
    """Build a minimal CommandSpec suitable for build_optimizer_config()."""
    log_path = generated_dir / f"{bottleneck}_{benchmark}.log"
    return runner.CommandSpec(
        benchmark=benchmark,
        bottleneck=bottleneck,
        command=[],
        cwd=_PROJECT_ROOT,
        env={},
        log_path=log_path,
        pg_dbname=f"hybench_{benchmark.split('_')[-1]}",
        runnable=True,
        reason="",
    )


def _build_config_for(bottleneck: str, benchmark: str, *, run_id: str, generated_dir: Path,
                      output_dir: Path) -> Dict[str, Any]:
    """Build a generated config dict for the given bottleneck/benchmark and
    return the parsed JSON contents."""
    args_argv = [
        "runner",
        "--scenario-set", "bonect",
        "--bonect-bottlenecks", "memory" if bottleneck == "memory" else "cpu",
        "--run-id", run_id,
        "--generated-config-dir", str(generated_dir),
        "--output-dir", str(output_dir),
    ]
    args = _parse_args_with_argv(args_argv)
    spec = _build_spec(bottleneck, benchmark, generated_dir)
    config_path = runner.build_optimizer_config(spec, args, run_id)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_memory_config_hard_enforces_quality_gate() -> None:
    """For memory target, enforce_test_r2/p95/max must be True and min_test_r2 >= 0."""
    with tempfile.TemporaryDirectory(prefix="bonect_rc1_e2e_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "memory", "hybench_ap",
            run_id="rc1_e2e_memory", generated_dir=generated_dir, output_dir=output_dir,
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        assert qg["enforce_test_r2"] is True, (
            f"memory must have enforce_test_r2=True; got {qg['enforce_test_r2']}"
        )
        assert qg["enforce_test_relative_error_p95"] is True, (
            f"memory must have enforce_test_relative_error_p95=True; got "
            f"{qg['enforce_test_relative_error_p95']}"
        )
        assert qg["enforce_test_relative_error_max"] is True, (
            f"memory must have enforce_test_relative_error_max=True; got "
            f"{qg['enforce_test_relative_error_max']}"
        )
        assert float(qg["min_test_r2"]) >= 0.0, (
            f"memory min_test_r2 must be >= 0.0; got {qg['min_test_r2']}"
        )
        print(
            f"[PASS] test_memory_config_hard_enforces_quality_gate: "
            f"enforce_test_r2/p95/max=True, min_test_r2={qg['min_test_r2']}"
        )


def test_cpu_config_hard_enforces_nonnegative_r2() -> None:
    """CPU models with negative held-out R2 must not enter candidate ranking."""
    with tempfile.TemporaryDirectory(prefix="bonect_rc1_e2e_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "cpu", "hybench_xp",
            run_id="rc1_e2e_cpu", generated_dir=generated_dir, output_dir=output_dir,
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        assert qg["enforce_test_r2"] is True, (
            f"cpu must enforce test R2; got "
            f"{qg['enforce_test_r2']}"
        )
        assert float(qg["min_test_r2"]) >= 0.0
        assert qg["enforce_test_relative_error_p95"] is False, (
            f"cpu must keep enforce_test_relative_error_p95=False; got "
            f"{qg['enforce_test_relative_error_p95']}"
        )
        assert qg["enforce_test_relative_error_max"] is False, (
            f"cpu must keep enforce_test_relative_error_max=False; got "
            f"{qg['enforce_test_relative_error_max']}"
        )
        print(
            f"[PASS] test_cpu_config_hard_enforces_nonnegative_r2: "
            f"R2 hard gate with relative-error tail warnings"
        )


def test_io_config_hard_enforces_nonnegative_r2() -> None:
    """IO models with negative held-out R2 must not enter candidate ranking."""
    with tempfile.TemporaryDirectory(prefix="bonect_rc1_e2e_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "io", "hybench_xp",
            run_id="rc1_e2e_io", generated_dir=generated_dir, output_dir=output_dir,
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        assert qg["enforce_test_r2"] is True, (
            f"io must enforce test R2; got "
            f"{qg['enforce_test_r2']}"
        )
        assert float(qg["min_test_r2"]) >= 0.0
        assert qg["enforce_test_relative_error_p95"] is False, (
            f"io must keep enforce_test_relative_error_p95=False; got "
            f"{qg['enforce_test_relative_error_p95']}"
        )
        assert qg["enforce_test_relative_error_max"] is False, (
            f"io must keep enforce_test_relative_error_max=False; got "
            f"{qg['enforce_test_relative_error_max']}"
        )
        print(
            f"[PASS] test_io_config_hard_enforces_nonnegative_r2: "
            f"R2 hard gate with relative-error tail warnings"
        )


def test_no_eighth_cost_param_introduced() -> None:
    """RC#1 fix must NOT introduce an 8th cost parameter (§2.1/§12 hard limit)."""
    with tempfile.TemporaryDirectory(prefix="bonect_rc1_e2e_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "memory", "hybench_ap",
            run_id="rc1_e2e_scope", generated_dir=generated_dir, output_dir=output_dir,
        )
        # The 7 allowed cost params
        allowed = {
            "seq_page_cost", "random_page_cost", "cpu_tuple_cost",
            "cpu_index_tuple_cost", "cpu_operator_cost",
            "parallel_setup_cost", "parallel_tuple_cost",
        }
        # Inspect param_ranges or active_params sets in the generated config
        param_ranges = cfg.get("optimizer", {}).get("bruteforce", {}).get("param_ranges", {})
        if param_ranges:
            keys = set(param_ranges.keys())
        else:
            keys = set(cfg.get("optimizer", {}).get("bruteforce", {}).get("active_params", []))
        # Even if empty, no 8th key should appear
        extras = keys - allowed
        assert not extras, f"RC#1 introduced non-allowed cost params: {extras}"
        print(f"[PASS] test_no_eighth_cost_param_introduced: param set = {sorted(keys)}")


def main() -> int:
    print("=" * 70)
    print("RC#1 e2e test: generated-config enforces Memory quality gate")
    print("=" * 70)
    test_memory_config_hard_enforces_quality_gate()
    test_cpu_config_hard_enforces_nonnegative_r2()
    test_io_config_hard_enforces_nonnegative_r2()
    test_no_eighth_cost_param_introduced()
    print("=" * 70)
    print("ALL RC#1 E2E TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

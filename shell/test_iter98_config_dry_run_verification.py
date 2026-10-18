"""iter98 generated config dry-run verification (override §499, §577-583).

Verifies that build_optimizer_config produces correct adaptive_replay_controller
settings when --adaptive-replay-controller-enabled is passed. Does NOT start a
run, does NOT write large jsonl, does NOT touch PG/qemu. Only writes a small
(<10KB) generated config JSON to config/generated/<dry_run_id>/.

Checks:
  1. adaptive_replay_controller.enabled == true
  2. adaptive_replay_controller.max_budget == 24
  3. adaptive_replay_controller.min_evidence == 12
  4. adaptive_replay_controller.batch_size == 6
  5. adaptive_replay_controller.max_expansions == 2
  6. adaptive_replay_controller.lane_quotas present
  7. candidate_replay_top_k == 24 (capped by max_budget)
  8. candidate_top_k >= 24 (pool >= max_budget, override §580-583 fix)
  9. small_loop_min_validated_candidate_count == 12 (decoupled)
 10. small_loop_abort_on_prefix_insufficient == true
 11. _env_bool("0") == False, _env_bool("false") == False (override §577-579 fix)

Run: python3 shell/test_iter98_config_dry_run_verification.py
"""
from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path
from typing import Any, Dict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the runner module's config builder and _env_bool
import importlib.util
_runner_path = os.path.join(_PROJECT_ROOT, "shell", "run_pg_benchmark_bottleneck_matrix.py")
_spec = importlib.util.spec_from_file_location("run_pg_benchmark_bottleneck_matrix", _runner_path)
_runner = importlib.util.module_from_spec(_spec)
# Register in sys.modules BEFORE exec so dataclass resolution can find the module
sys.modules["run_pg_benchmark_bottleneck_matrix"] = _runner
_spec.loader.exec_module(_runner)

from run_pg_benchmark_bottleneck_matrix import (
    build_optimizer_config,
    CommandSpec,
    _env_bool,
    parse_args,
)
import json


def _make_dry_run_args() -> argparse.Namespace:
    """Build args namespace by parsing a minimal CLI string, then overriding
    adaptive replay controller fields. This avoids missing-defaults errors."""
    # Start with the parser's defaults by parsing a minimal bonect memory scenario
    cli = [
        "--scenario-set", "bonect",
        "--bonect-bottlenecks", "memory",
        "--run-id", "iter98_dry_run_verification_20260706",
        "--optimizer-mode", "bruteforce",
        "--use-resource-estimator",
        "--final-validation",
        "--fail-fast",
        "--adaptive-replay-controller-enabled",
        "--adaptive-replay-controller-max-budget", "24",
        "--adaptive-replay-controller-min-evidence", "12",
        "--adaptive-replay-controller-batch-size", "6",
        "--adaptive-replay-controller-max-expansions", "2",
        # Memory scenario VM profile
        "--vm-cpu-cpu", "14",
        "--vm-cpu-mem", "4G",
        "--vm-cpu-read-mb", "800",
        "--vm-cpu-write-mb", "800",
    ]
    args = parse_args.__wrapped__() if hasattr(parse_args, "__wrapped__") else parse_args()
    # parse_args() with no args would fail (required args missing); instead
    # parse our CLI via the parser directly
    # Re-import the parser by calling parse_args with sys.argv override
    import run_pg_benchmark_bottleneck_matrix as runner_mod
    # Build parser fresh
    old_argv = sys.argv
    try:
        sys.argv = ["run_pg_benchmark_bottleneck_matrix.py"] + cli
        args = runner_mod.parse_args()
    finally:
        sys.argv = old_argv
    return args


def test_env_bool_fix():
    """Override §577-579: _env_bool must treat '0'/'false' as False."""
    # Save and set env vars
    old = os.environ.get("ADAPTIVE_REPLAY_CONTROLLER_ENABLED")
    try:
        for falsy in ("0", "false", "no", "off", ""):
            os.environ["ADAPTIVE_REPLAY_CONTROLLER_ENABLED"] = falsy
            result = _env_bool("ADAPTIVE_REPLAY_CONTROLLER_ENABLED", False)
            assert result is False, (
                f"_env_bool({falsy!r}) should be False, got {result}"
            )
        for truthy in ("1", "true", "yes", "on"):
            os.environ["ADAPTIVE_REPLAY_CONTROLLER_ENABLED"] = truthy
            result = _env_bool("ADAPTIVE_REPLAY_CONTROLLER_ENABLED", False)
            assert result is True, (
                f"_env_bool({truthy!r}) should be True, got {result}"
            )
        # Unset → default
        del os.environ["ADAPTIVE_REPLAY_CONTROLLER_ENABLED"]
        result = _env_bool("ADAPTIVE_REPLAY_CONTROLLER_ENABLED", False)
        assert result is False, "unset env should return default False"
        result_default_true = _env_bool("MISSING_VAR_xyz", True)
        assert result_default_true is True, "unset env should return default True"
    finally:
        if old is not None:
            os.environ["ADAPTIVE_REPLAY_CONTROLLER_ENABLED"] = old
        elif "ADAPTIVE_REPLAY_CONTROLLER_ENABLED" in os.environ:
            del os.environ["ADAPTIVE_REPLAY_CONTROLLER_ENABLED"]
    print("[PASS] test_env_bool_fix: '0'/'false'→False, '1'/'true'→True (override §577-579)")


def test_generated_config_adaptive_block():
    """Override §499 + §580-583: generated config must have correct adaptive settings."""
    spec = CommandSpec(
        benchmark="hybench_ap",
        bottleneck="memory",
        command=[],
        cwd=Path(_PROJECT_ROOT),
        env={},
        log_path=Path("/tmp/iter98_dry_run_unused.log"),
        pg_dbname="bonect_metrics",
    )
    args = _make_dry_run_args()
    dry_run_id = "iter98_dry_run_verification_20260706"
    config_path = build_optimizer_config(spec, args, dry_run_id)
    assert config_path.exists(), f"config not written: {config_path}"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 1-6: adaptive_replay_controller block
    arc = cfg.get("optimizer", {}).get("bruteforce", {}).get("adaptive_replay_controller")
    assert isinstance(arc, dict), f"adaptive_replay_controller block missing; got keys={list(cfg.get('optimizer', {}).get('bruteforce', {}).keys())}"
    assert arc.get("enabled") is True, f"adaptive_replay_controller.enabled should be True; got {arc.get('enabled')}"
    assert arc.get("max_budget") == 24, f"max_budget should be 24; got {arc.get('max_budget')}"
    assert arc.get("min_evidence") == 12, f"min_evidence should be 12; got {arc.get('min_evidence')}"
    assert arc.get("batch_size") == 6, f"batch_size should be 6; got {arc.get('batch_size')}"
    assert arc.get("max_expansions") == 2, f"max_expansions should be 2; got {arc.get('max_expansions')}"
    lane_quotas = arc.get("lane_quotas")
    assert isinstance(lane_quotas, dict), f"lane_quotas should be dict; got {type(lane_quotas)}"
    for lane in ("explainable", "fallback", "diagnostic", "mandatory_anchor", "reserve"):
        assert lane in lane_quotas, f"lane_quotas missing {lane}; got {list(lane_quotas.keys())}"

    # 7-10: candidate_replay_top_k / candidate_top_k / min_validated / abort
    bf = cfg.get("optimizer", {}).get("bruteforce", {})
    candidate_replay_top_k = bf.get("candidate_replay_top_k")
    candidate_top_k = bf.get("candidate_top_k")
    min_validated = bf.get("small_loop_min_validated_candidate_count")
    abort = bf.get("small_loop_abort_on_prefix_insufficient")
    assert candidate_replay_top_k == 24, (
        f"candidate_replay_top_k should be 24 (capped by max_budget); got {candidate_replay_top_k}"
    )
    assert candidate_top_k >= 24, (
        f"candidate_top_k should be >= 24 (pool >= max_budget, override §580-583); got {candidate_top_k}"
    )
    assert min_validated == 12, (
        f"small_loop_min_validated_candidate_count should be 12 (decoupled); got {min_validated}"
    )
    assert abort is True, (
        f"small_loop_abort_on_prefix_insufficient should be True; got {abort}"
    )

    print(f"[PASS] test_generated_config_adaptive_block: config at {config_path}")
    print(f"       candidate_top_k={candidate_top_k} (>=24 ✓)")
    print(f"       candidate_replay_top_k={candidate_replay_top_k} (==24 ✓)")
    print(f"       min_validated={min_validated} (==12 ✓)")
    print(f"       abort_on_prefix_insufficient={abort} (==True ✓)")
    print(f"       lane_quotas={lane_quotas}")


def main() -> int:
    print("=" * 70)
    print("iter98 config dry-run verification (override §499 + §577-583)")
    print("=" * 70)
    test_env_bool_fix()
    test_generated_config_adaptive_block()
    print("=" * 70)
    print("ALL iter98 CONFIG DRY-RUN TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

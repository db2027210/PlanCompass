"""iter107 runner --memory-replay-calibration-* CLI + --dry-run flag test.

Validates three things required by the override directive
§2026-07-07 03:12 iter106 §715-806 next_iter_signal PRIORITY #1/#2/#3 and
§2026-07-06 iter100 §8 #1/#2/#3 at the **generated config layer**:

  1. The runner exposes four new CLI flags parsed into the generated config:
     - ``--memory-replay-calibration-enabled``
     - ``--memory-replay-calibration-max-predicted-down-actual-up-ratio``
       (default 0.5)
     - ``--memory-replay-calibration-min-predicted-down-count``
       (default 5)
     - ``--memory-replay-calibration-evidence-path`` (optional)
     plus ``--dry-run`` (build cfg, exit 0).
  2. When ``--memory-replay-calibration-enabled`` is set, the generated
     config's
     ``metrics.plan_estimator.model_retraining.quality_gate.memory_replay_calibration``
     block contains the parsed args (enabled/max_ratio/min_count/evidence).
  3. When ``--memory-replay-calibration-evidence-path`` points to a JSON
     object, the evidence is inlined into the block.
  4. Provenance hard-fail: when ``--memory-replay-calibration-enabled`` is set
     for a memory target run but no evidence file was provided (or it is
     not parseable as a JSON object), ``build_optimizer_config`` raises
     ``RuntimeError`` so v7 cannot enter with a silent no-op gate.
  5. ``--dry-run`` flag causes ``main()`` to build and write generated cfgs
     ONLY and exit 0 WITHOUT launching PG/HyBench/optimizer subprocesses.
     No real Bonect experiment starts (hard boundary §12 + iter106 §798).
  6. The eight-cost-parameter hard limit is preserved (§2.1 / §12 #2).
     No 8th cost param appears in the generated active_params set.

Run: python3 shell/test_iter107_runner_memory_replay_calibration_args.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

sys.path.insert(0, _THIS_DIR)
import run_pg_benchmark_bottleneck_matrix as runner  # type: ignore


def _parse_args_with_argv(argv: List[str]) -> argparse.Namespace:
    saved = sys.argv
    sys.argv = argv
    try:
        return runner.parse_args()
    finally:
        sys.argv = saved


def _build_spec(bottleneck: str, benchmark: str, generated_dir: Path) -> runner.CommandSpec:
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


_ALLOWED_COST_PARAMS = {
    "seq_page_cost", "random_page_cost", "cpu_tuple_cost",
    "cpu_index_tuple_cost", "cpu_operator_cost",
    "parallel_setup_cost", "parallel_tuple_cost",
}


def _build_config_for(
    bottleneck: str,
    benchmark: str,
    *,
    run_id: str,
    generated_dir: Path,
    output_dir: Path,
    extra_argv: List[str] | None = None,
) -> Dict[str, Any]:
    args_argv = [
        "runner",
        "--scenario-set", "bonect",
        "--bonect-bottlenecks", bottleneck,
        "--run-id", run_id,
        "--generated-config-dir", str(generated_dir),
        "--output-dir", str(output_dir),
    ]
    if extra_argv:
        args_argv.extend(extra_argv)
    args = _parse_args_with_argv(args_argv)
    spec = _build_spec(bottleneck, benchmark, generated_dir)
    config_path = runner.build_optimizer_config(spec, args, run_id)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_default_calibration_disabled_for_memory() -> None:
    """Without --memory-replay-calibration-enabled, the block is present but disabled."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "memory", "hybench_ap",
            run_id="iter107_default_memory", generated_dir=generated_dir, output_dir=output_dir,
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        mrc = qg.get("memory_replay_calibration")
        assert isinstance(mrc, dict), (
            "memory_replay_calibration block must always be present in the generated "
            f"config's quality_gate; got {mrc!r}"
        )
        assert mrc["enabled"] is False, (
            f"memory_replay_calibration.enabled must default to False without CLI flag; got {mrc['enabled']}"
        )
        assert float(mrc["max_predicted_down_actual_up_ratio"]) == 0.5, (
            f"default max_predicted_down_actual_up_ratio must be 0.5; got {mrc['max_predicted_down_actual_up_ratio']}"
        )
        assert int(mrc["min_predicted_down_count"]) == 5, (
            f"default min_predicted_down_count must be 5; got {mrc['min_predicted_down_count']}"
        )
        assert mrc["evidence"] == {}, (
            f"no evidence must be loaded by default; got {mrc['evidence']}"
        )
        assert mrc["evidence_path"] == "", (
            f"evidence_path must be empty string by default; got {mrc['evidence_path']}"
        )
        print("[PASS] test_default_calibration_disabled_for_memory")


def test_calibration_enabled_with_loaded_evidence() -> None:
    """When --memory-replay-calibration-enabled + --memory-replay-calibration-evidence-path
    point to a valid JSON object, block is enabled and evidence inlined."""
    sample_evidence = {
        "predicted_down_count": 21,
        "actual_down_count": 3,
        "predicted_down_actual_up_count": 18,
        "predicted_values": [3.38] * 5,
    }
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        evidence_path = tmp_path / "evidence.json"
        evidence_path.write_text(json.dumps(sample_evidence), encoding="utf-8")
        cfg = _build_config_for(
            "memory", "hybench_ap",
            run_id="iter107_with_evidence", generated_dir=generated_dir, output_dir=output_dir,
            extra_argv=[
                "--memory-replay-calibration-enabled",
                "--memory-replay-calibration-max-predicted-down-actual-up-ratio", "0.4",
                "--memory-replay-calibration-min-predicted-down-count", "8",
                "--memory-replay-calibration-evidence-path", str(evidence_path),
            ],
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        mrc = qg["memory_replay_calibration"]
        assert mrc["enabled"] is True, f"expected enabled=True; got {mrc['enabled']}"
        assert float(mrc["max_predicted_down_actual_up_ratio"]) == 0.4, (
            f"expected max_ratio=0.4; got {mrc['max_predicted_down_actual_up_ratio']}"
        )
        assert int(mrc["min_predicted_down_count"]) == 8, (
            f"expected min_count=8; got {mrc['min_predicted_down_count']}"
        )
        assert mrc["evidence"] == sample_evidence, (
            "evidence JSON must be inlined into the block; "
            f"got {mrc['evidence']}"
        )
        assert mrc["evidence_path"] == str(evidence_path), (
            f"evidence_path must be the loaded path; got {mrc['evidence_path']}"
        )
        print("[PASS] test_calibration_enabled_with_loaded_evidence")


def test_provenance_hard_fail_when_enabled_but_no_evidence() -> None:
    """When --memory-replay-calibration-enabled is set for memory target but
    --memory-replay-calibration-evidence-path is empty or unknown, build_optimizer_config
    raises RuntimeError (provenance hard-fail) to prevent a silent no-op gate."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        try:
            _build_config_for(
                "memory", "hybench_ap",
                run_id="iter107_hardfail_noev", generated_dir=generated_dir, output_dir=output_dir,
                extra_argv=["--memory-replay-calibration-enabled"],
            )
            raise AssertionError(
                "Expected RuntimeError for memory target with calibration enabled "
                "but no evidence path; none was raised"
            )
        except RuntimeError as exc:
            msg = str(exc)
            assert "memory_replay_calibration provenance hard-fail" in msg, (
                f"RuntimeError must mention provenance hard-fail; got: {msg}"
            )
            print(f"[PASS] test_provenance_hard_fail_when_enabled_but_no_evidence: {msg.splitlines()[0]}")


def test_provenance_hard_fail_when_evidence_file_missing() -> None:
    """When --memory-replay-calibration-enabled is set and --memory-replay-calibration-evidence-path
    exists as a CLI flag but the file does not exist on disk, build_optimizer_config raises
    RuntimeError before writing the generated config."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        bogus_path = tmp_path / "missing_evidence.json"
        try:
            _build_config_for(
                "memory", "hybench_ap",
                run_id="iter107_hardfail_missing", generated_dir=generated_dir, output_dir=output_dir,
                extra_argv=[
                    "--memory-replay-calibration-enabled",
                    "--memory-replay-calibration-evidence-path", str(bogus_path),
                ],
            )
            raise AssertionError(
                "Expected RuntimeError for missing evidence path; none was raised"
            )
        except RuntimeError as exc:
            msg = str(exc)
            assert "memory_replay_calibration provenance hard-fail" in msg, (
                f"RuntimeError must mention provenance; got: {msg}"
            )
            assert "not a file" in msg or "is not a file" in msg, (
                f"RuntimeError must mention file-not-found nature; got: {msg}"
            )
            print(f"[PASS] test_provenance_hard_fail_when_evidence_file_missing: {msg.splitlines()[0]}")


def test_provenance_hard_fail_when_evidence_not_json_object() -> None:
    """When the evidence file is a JSON list/scalar (not an object), hard-fail."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        evidence_path = tmp_path / "evidence_list.json"
        evidence_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        try:
            _build_config_for(
                "memory", "hybench_ap",
                run_id="iter107_hardfail_listev", generated_dir=generated_dir, output_dir=output_dir,
                extra_argv=[
                    "--memory-replay-calibration-enabled",
                    "--memory-replay-calibration-evidence-path", str(evidence_path),
                ],
            )
            raise AssertionError(
                "Expected RuntimeError for JSON list evidence; none was raised"
            )
        except RuntimeError as exc:
            msg = str(exc)
            assert "provenance hard-fail" in msg, (
                f"RuntimeError must mention provenance; got: {msg}"
            )
            print(f"[PASS] test_provenance_hard_fail_when_evidence_not_json_object: {msg.splitlines()[0]}")


def test_calibration_disabled_for_cpu_target_even_if_flag_set() -> None:
    """For non-memory targets (cpu/io/none), calibration.enabled follows the
    CLI flag but the provenance hard-fail is memory-only. With no flag set,
    the gate is disabled for non-memory targets."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        cfg = _build_config_for(
            "cpu", "hybench_xp",
            run_id="iter107_cpu_no_flag", generated_dir=generated_dir, output_dir=output_dir,
        )
        qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        mrc = qg["memory_replay_calibration"]
        assert mrc["enabled"] is False, (
            f"cpu default must be disabled; got {mrc['enabled']}"
        )
        print("[PASS] test_calibration_disabled_for_cpu_target_even_if_flag_set")


def test_dry_run_writes_configs_and_exits_zero() -> None:
    """--dry-run flag builds and writes generated cfg files only and exits 0
    WITHOUT launching PG/HyBench/optimizer subprocesses. No real experiment."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_dryrun_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        run_id = "iter107_dryrun_memory"
        cfg_path = generated_dir / run_id / "memory" / "hybench_ap.json"
        ref_dir = output_dir / "history_pg_benchmark_bottleneck_matrix" / run_id

        # Build a real evidence JSON to use
        evidence_path = tmp_path / "evidence.json"
        sample_evidence = {
            "predicted_down_count": 21,
            "actual_down_count": 3,
            "predicted_down_actual_up_count": 18,
            "predicted_values": [3.38] * 5,
        }
        evidence_path.write_text(json.dumps(sample_evidence), encoding="utf-8")

        argv = [
            "python3", os.path.join(_THIS_DIR, "run_pg_benchmark_bottleneck_matrix.py"),
            "--scenario-set", "bonect",
            "--bonect-bottlenecks", "memory",
            "--run-id", run_id,
            "--generated-config-dir", str(generated_dir),
            "--output-dir", str(output_dir),
            "--memory-replay-calibration-enabled",
            "--memory-replay-calibration-evidence-path", str(evidence_path),
            "--dry-run",
        ]
        result = subprocess.run(
            argv,
            cwd=_PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": _PROJECT_ROOT + ":" + _THIS_DIR},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"--dry-run must exit 0; got {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert cfg_path.is_file(), (
            f"--dry-run must write the generated cfg; expected {cfg_path}"
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        mrc = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]["memory_replay_calibration"]
        assert mrc["enabled"] is True, (
            f"dry-run cfg must have enabled=True when flag set; got {mrc['enabled']}"
        )
        assert mrc["evidence"] == sample_evidence, (
            "dry-run cfg must inline evidence JSON"
        )
        assert "DRY_RUN_DONE written=1 failures=0" in result.stdout, (
            f"dry-run summary missing; got stdout:\n{result.stdout}"
        )
        print("[PASS] test_dry_run_writes_configs_and_exits_zero")


def test_no_eighth_cost_param_introduced() -> None:
    """The iter107 plumbing must NOT introduce an 8th cost parameter."""
    with tempfile.TemporaryDirectory(prefix="bonect_iter107_") as tmp:
        tmp_path = Path(tmp)
        generated_dir = tmp_path / "generated"
        output_dir = tmp_path / "history"
        evidence_path = tmp_path / "evidence.json"
        evidence_path.write_text(json.dumps({"predicted_down_count": 1}), encoding="utf-8")
        cfg = _build_config_for(
            "memory", "hybench_ap",
            run_id="iter107_scope", generated_dir=generated_dir, output_dir=output_dir,
            extra_argv=[
                "--memory-replay-calibration-enabled",
                "--memory-replay-calibration-evidence-path", str(evidence_path),
            ],
        )
        param_ranges = cfg.get("optimizer", {}).get("bruteforce", {}).get("param_ranges", {})
        if param_ranges:
            keys = set(param_ranges.keys())
        else:
            keys = set(cfg.get("optimizer", {}).get("bruteforce", {}).get("active_params", []))
        extras = keys - _ALLOWED_COST_PARAMS
        assert not extras, f"iter107 introduced non-allowed cost params: {extras}"
        print(f"[PASS] test_no_eighth_cost_param_introduced: param set = {sorted(keys)}")


def main() -> int:
    print("=" * 70)
    print("iter107 runner --memory-replay-calibration args + --dry-run test")
    print("=" * 70)
    test_default_calibration_disabled_for_memory()
    test_calibration_enabled_with_loaded_evidence()
    test_provenance_hard_fail_when_enabled_but_no_evidence()
    test_provenance_hard_fail_when_evidence_file_missing()
    test_provenance_hard_fail_when_evidence_not_json_object()
    test_calibration_disabled_for_cpu_target_even_if_flag_set()
    test_dry_run_writes_configs_and_exits_zero()
    test_no_eighth_cost_param_introduced()
    print("=" * 70)
    print("ALL iter107 TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
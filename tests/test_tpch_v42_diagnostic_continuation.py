from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/run_tpch_round29_diagnostic_exact_plan_regime_batch_v42.py"
CONTROLLER = ROOT / "tools/tpch_v42_complete_cycle004_diagnostics.py"


def import_path(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_none_success_is_normalized_to_zero() -> None:
    runner = import_path("tpch_v42_runner_test", RUNNER)
    assert runner.normalize_exit_code(None) == 0
    assert runner.normalize_exit_code(0) == 0
    try:
        runner.normalize_exit_code(1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("nonzero sealed runner status must fail closed")


def test_wrapper_bypasses_only_the_broken_v40_int_none_layer() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "normalize_exit_code(v40.original.main())" in source
    assert "v40.main()" not in source
    assert "V40_SHA256" in source


def test_controller_has_no_formal_execution_call() -> None:
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "analyze_gate" not in called_attributes
    assert "promote_formal" not in called_attributes
    assert "run_formal" not in called_attributes


def test_exact_is_built_and_run_before_robust() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    exact_build = source.index("exact = v26.build_exact_repeat(")
    exact_run = source.index("v26.run_clean_diagnostic(", exact_build)
    robust_build = source.index("robust = v26.build_robust_repeat(")
    robust_run = source.index("v26.run_clean_diagnostic(", robust_build)
    audit = source.index('"status": "complete_robust_ready_for_v41"')
    assert exact_build < exact_run < robust_build < robust_run < audit


def test_partial_phase_is_never_retried() -> None:
    controller = import_path("tpch_v42_controller_test", CONTROLLER)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selection = root / "selection.json"
        second = root / "selection.second.json"
        run_root = root / "run"
        anchor = root / "anchor.json"
        anchor.write_text("{}\n", encoding="utf-8")
        selection.write_text("{}\n", encoding="utf-8")
        assert not second.exists() and not run_root.exists()
        try:
            controller.require_absent_or_clean(
                selection, second, run_root, anchor
            )
        except RuntimeError as error:
            assert "partial diagnostic phase cannot be retried" in str(error)
        else:
            raise AssertionError("partial phase must fail closed")


def test_contract_preserves_fresh0_and_no_manual_candidates() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    assert '"accepted_count": 0' in source
    assert '"new_physical_calls": 10' in source
    assert '"formal_started": False' in source
    assert '"formal_physical_calls": 0' in source
    assert '"manual_candidate_injection": False' in source


def main() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"TPC-H v42 offline tests passed: {len(tests)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

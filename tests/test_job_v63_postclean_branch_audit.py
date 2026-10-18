from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import job_fresh10_target10_autonomous_v63 as v63  # noqa: E402


V50 = TOOLS / "job_fresh10_target10_autonomous_v50.py"
V49 = TOOLS / "job_fresh10_target10_autonomous_v49.py"
ROBUST_V11 = TOOLS / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py"
FORMAL_V2 = TOOLS / "job_formal_target10_batch_controller_v2_gradient_commit.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def expect_fail_closed(callable_value, fragment: str) -> None:
    try:
        callable_value()
    except v63.implementation.v49.FailClosed as error:
        assert fragment in str(error), str(error)
        return
    raise AssertionError("unsafe branch unexpectedly passed")


def test_five_clean_gate_misses_are_a_known_autonomy_stop() -> None:
    """Record the v63 gap: clean measurements can still exhaust admission."""
    implementation = v63.implementation
    v49 = implementation.v49
    saved = (
        v49.build_repeat,
        v49.exact_parameter_set,
        implementation.run_diagnostic_v50,
        implementation.evaluate_gate_v50,
    )
    calls = {"diagnostic": 0, "gate": 0}
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery = root / "discovery"
            write_json(discovery / "top5.json", {"top5": []})

            def build_repeat(_source: Path, target: Path) -> Path:
                selection = target / "top5.json"
                write_json(selection, {"top5": []})
                return selection

            def run_diagnostic(*_args):
                calls["diagnostic"] += 1
                return True, {"technical_failure_count": 0}

            def evaluate_gate(target: Path):
                calls["gate"] += 1
                gate = target / "entry_gate_raw.json"
                write_json(gate, {"passed": False, "checks": {"rank": False}})
                return False, gate

            v49.build_repeat = build_repeat
            v49.exact_parameter_set = lambda _path: {"same-exact-points"}
            implementation.run_diagnostic_v50 = run_diagnostic
            implementation.evaluate_gate_v50 = evaluate_gate
            expect_fail_closed(
                lambda: implementation.obtain_passed_repeat_v50(discovery, root),
                "five clean5 repeat gate attempts exhausted",
            )
            assert calls == {"diagnostic": 5, "gate": 5}
    finally:
        (
            v49.build_repeat,
            v49.exact_parameter_set,
            implementation.run_diagnostic_v50,
            implementation.evaluate_gate_v50,
        ) = saved


def test_partial_formal_is_an_intentional_at_most_once_stop() -> None:
    """A replay directory without a commit must never be auto-replayed."""
    v49 = v63.implementation.v49
    with tempfile.TemporaryDirectory() as directory:
        formal = Path(directory) / "formal"
        (formal / "replay").mkdir(parents=True)
        expect_fail_closed(
            lambda: v49.execute_formal(formal, "a" * 64),
            "partial formal replay cannot be repeated",
        )


def test_committed_formal_is_idempotently_resealed_without_workload() -> None:
    """A committed transaction takes the validation-only resume branch."""
    v49 = v63.implementation.v49
    saved = v49.validate_committed_formal
    observed: list[Path] = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            formal = Path(directory) / "formal"
            transaction = formal / "replay/formal_state_commit_transaction.json"
            write_json(transaction, {"status": "committed"})
            v49.validate_committed_formal = lambda root: observed.append(root)
            v49.execute_formal(formal, "a" * 64)
            assert observed == [formal]
    finally:
        v49.validate_committed_formal = saved


def test_neutrality_and_continue_guards_remain_sealed_in_v63_dependencies() -> None:
    """Pin the post-clean source contracts inherited by the live v63 process."""
    v50 = V50.read_text(encoding="utf-8")
    v49 = V49.read_text(encoding="utf-8")
    robust = ROBUST_V11.read_text(encoding="utf-8")
    formal = FORMAL_V2.read_text(encoding="utf-8")

    for required in (
        'item.get("accepted") is False',
        'item.get("gradient_update_applied") is False',
        'item.get("incumbent_update_applied") is False',
        'item.get("range_update_applied") is False',
        "sha256_file(v49.STATE) != state_sha_before",
        'phase="fresh_discovery_after_repeat_technical"',
        "except v49.NoveltyCollision as error:",
        '"status": "abandoned_zero_formal_calls_fingerprint_collision"',
    ):
        assert required in v50, required
    for required in (
        "partial diagnostic replay cannot be repeated",
        "partial formal replay cannot be repeated",
        "validate_committed_formal(formal)",
    ):
        assert required in v49, required
    for required in (
        "rejected/probe rank",
        "technical rank",
        "after != before",
    ):
        assert required in robust, required
    for required in (
        "gradient commit evidence is incomplete",
        "after-commit gradient verification failed",
        "v58 base formal controller returned",
    ):
        assert required in formal, required


def main() -> int:
    test_five_clean_gate_misses_are_a_known_autonomy_stop()
    test_partial_formal_is_an_intentional_at_most_once_stop()
    test_committed_formal_is_idempotently_resealed_without_workload()
    test_neutrality_and_continue_guards_remain_sealed_in_v63_dependencies()
    print(
        "JOB v63 post-clean branch audit tests passed: 4; "
        f"v63_sha256={sha(TOOLS / 'job_fresh10_target10_autonomous_v63.py')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

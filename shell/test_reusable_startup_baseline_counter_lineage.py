#!/usr/bin/env python3
"""Reusable startup-baseline counter-lineage regression tests."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from typing import Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parameter_optimizer import (  # noqa: E402
    validate_reusable_startup_baseline_counter_lineage,
)


def _protocol(root: Path, values: Dict[str, int]) -> Dict[str, object]:
    paths = {}
    for name, value in values.items():
        path = root / f"{name}.txt"
        path.write_text(f"{value}\n", encoding="utf-8")
        paths[name] = str(path)
    return {
        "global_replay_ordinal_at_binding": 49,
        "global_replay_counter_path": paths["global_replay_counter_path"],
        "formal_search_replay_count_start": 1,
        "formal_search_replay_counter_path": paths[
            "formal_search_replay_counter_path"
        ],
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_counter_path": paths[
            "formal_search_accepted_counter_path"
        ],
        "formal_startup_baseline_counter_path": paths[
            "formal_startup_baseline_counter_path"
        ],
        "formal_search_maximum_replay_count": 130,
        "formal_search_accepted_target": 10,
    }


def _values(
    *,
    global_count: int = 50,
    replay_count: int = 2,
    accepted_count: int = 0,
    startup_count: int = 1,
) -> Dict[str, int]:
    return {
        "global_replay_counter_path": global_count,
        "formal_search_replay_counter_path": replay_count,
        "formal_search_accepted_counter_path": accepted_count,
        "formal_startup_baseline_counter_path": startup_count,
    }


def _expect_failure(protocol: Dict[str, object], expected: str) -> None:
    try:
        validate_reusable_startup_baseline_counter_lineage(
            protocol=protocol,
            startup_baseline_reuse_cfg={
                "startup_baseline_count_before_restart": 1,
            },
        )
    except RuntimeError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected RuntimeError containing {expected!r}")


def test_progressed_candidate_counters_are_valid() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        result = validate_reusable_startup_baseline_counter_lineage(
            protocol=_protocol(Path(temp_dir), _values()),
            startup_baseline_reuse_cfg={
                "startup_baseline_count_before_restart": 1,
            },
        )
    assert result["global_binding"] == 49
    assert result["global_current"] == 50
    assert result["formal_search_start"] == 1
    assert result["formal_search_current"] == 2
    assert result["replay_progress"] == 1


def test_technical_attempt_progress_may_exceed_success_progress() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        result = validate_reusable_startup_baseline_counter_lineage(
            protocol=_protocol(
                Path(temp_dir),
                _values(global_count=51, replay_count=2),
            ),
            startup_baseline_reuse_cfg={
                "startup_baseline_count_before_restart": 1,
            },
        )
        assert result["attempt_progress"] == 2
        assert result["replay_progress"] == 1


def test_success_progress_cannot_exceed_global_attempt_progress() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        protocol = _protocol(
            Path(temp_dir),
            _values(global_count=50, replay_count=3),
        )
        _expect_failure(protocol, "lineage diverged")


def test_counter_regression_and_duplicate_startup_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        regressed = _protocol(
            Path(temp_dir),
            _values(global_count=48, replay_count=1),
        )
        _expect_failure(regressed, "global counter regressed")
    with tempfile.TemporaryDirectory() as temp_dir:
        duplicate_startup = _protocol(
            Path(temp_dir),
            _values(startup_count=2),
        )
        _expect_failure(duplicate_startup, "startup baseline counter is not 1")


def main() -> None:
    test_progressed_candidate_counters_are_valid()
    test_technical_attempt_progress_may_exceed_success_progress()
    test_success_progress_cannot_exceed_global_attempt_progress()
    test_counter_regression_and_duplicate_startup_are_rejected()
    print("reusable startup baseline counter-lineage tests passed", flush=True)


if __name__ == "__main__":
    main()

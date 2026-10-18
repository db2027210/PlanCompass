#!/usr/bin/env python3
"""Recover an XP-I/O scene after one max-duration unstable candidate."""

import json
from pathlib import Path
import sys

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v7 as predecessor


ERROR = "RuntimeError: outer 11 fallback_plan_change wave 1 failed closed"
stale_dispatch = predecessor.predecessor.predecessor


def _with_error(call):
    old = set(stale_dispatch.STALE_TASK_ERRORS)
    stale_dispatch.STALE_TASK_ERRORS.add(ERROR)
    try:
        return call()
    finally:
        stale_dispatch.STALE_TASK_ERRORS.clear()
        stale_dispatch.STALE_TASK_ERRORS.update(old)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_source(run_root: Path) -> None:
    state = _load(run_root / "run_state.json")
    batch = _load(run_root / "outer_011/fallback_plan_change/wave_1/batch_result.json")
    measurements = dict(batch.get("measurements") or {})
    invalid = []
    valid = []
    for worker_id, row in measurements.items():
        checks = dict(row.get("hard_gate_checks") or {})
        failed = {name for name, value in checks.items() if value is False}
        stability = dict(row.get("resource_stability") or {})
        if row.get("hard_gate_passed") is True and row.get("coverage_complete") is True:
            valid.append(worker_id)
        elif (
            row.get("coverage_complete") is True
            and failed == {"target_resource_stability_passed"}
            and stability.get("stable") is False
            and float(stability.get("selected_measurement_seconds") or 0) == 900.0
            and float(stability.get("maximum_measurement_seconds") or 0) == 900.0
        ):
            invalid.append(worker_id)
        else:
            raise RuntimeError("XP-I/O recovery batch failure shape differs")
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != ERROR
        or int(state.get("outer_attempt") or 0) != 10
        or batch.get("status") != "FAILED_CLOSED"
        or batch.get("failures") not in ([], None)
        or set(measurements) != {"xp_io_w1", "xp_io_w2"}
        or len(valid) != 1
        or len(invalid) != 1
    ):
        raise RuntimeError("XP-I/O max-duration instability boundary differs")


def verify_checkpoint(path: Path):
    return _with_error(lambda: predecessor.verify_checkpoint(path))


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path):
        return _with_error(lambda: predecessor.scene_checkpoint.verify_or_standard(path))


scene_checkpoint = _CheckpointVerifier()


def main() -> int:
    if "--run-root" not in sys.argv:
        raise RuntimeError("explicit source run root required")
    run_root = Path(sys.argv[sys.argv.index("--run-root") + 1]).resolve()
    _verify_source(run_root)
    return int(_with_error(predecessor.main))


if __name__ == "__main__":
    raise SystemExit(main())


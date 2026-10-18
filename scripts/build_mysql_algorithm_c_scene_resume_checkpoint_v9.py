#!/usr/bin/env python3
"""Recover XP-CPU after bounded Docker-control serialization expiry."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v7 as predecessor


ERROR = "TimeoutError: Docker control API serialization timeout"
error_dispatch = predecessor.predecessor.predecessor


def _with_error(call):
    old = set(error_dispatch.STALE_TASK_ERRORS)
    error_dispatch.STALE_TASK_ERRORS.add(ERROR)
    try:
        return call()
    finally:
        error_dispatch.STALE_TASK_ERRORS.clear()
        error_dispatch.STALE_TASK_ERRORS.update(old)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_source(run_root: Path) -> None:
    state = _load(run_root / "run_state.json")
    completed = int(state.get("outer_attempt") or 0)
    incomplete = run_root / f"outer_{completed + 1:03d}"
    first_batch = incomplete / "top5_batch_1/batch_result.json"
    second_batch = incomplete / "top5_batch_2/batch_result.json"
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != ERROR
        or state.get("active_scenes") != ["hybench_xp_cpu"]
        or completed != 13
        or not first_batch.is_file()
        or second_batch.exists()
        or any(incomplete.glob("*.decision.json"))
    ):
        raise RuntimeError("XP-CPU Docker-control recovery boundary differs")
    batch = _load(first_batch)
    measurements = dict(batch.get("measurements") or {})
    if (
        batch.get("status") != "PASS"
        or set(measurements) != {"xp_cpu_w1", "xp_cpu_w2"}
        or not all(
            row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
            for row in measurements.values()
        )
    ):
        raise RuntimeError("XP-CPU completed sibling batch differs")


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

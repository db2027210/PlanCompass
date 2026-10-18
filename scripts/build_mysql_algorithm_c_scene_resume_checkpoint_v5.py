#!/usr/bin/env python3
"""Scene checkpoint dispatcher for verified stale-containerd-task failures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v4 as predecessor


STALE_TASK_ERRORS = {
    (
        "CalledProcessError: Command '['docker', 'start', "
        f"'ra_mysql_disk2_algc_{suffix}_w{worker}_20260809']' returned "
        "non-zero exit status 1."
    )
    for suffix in ("xp_cpu", "xp_io")
    for worker in (1, 2)
}


def _with_stale_errors(call):
    old = set(predecessor.RECOVERABLE_V110_ERRORS)
    predecessor.RECOVERABLE_V110_ERRORS.update(STALE_TASK_ERRORS)
    try:
        return call()
    finally:
        predecessor.RECOVERABLE_V110_ERRORS.clear()
        predecessor.RECOVERABLE_V110_ERRORS.update(old)


def verify_any_checkpoint(path: Path):
    return _with_stale_errors(lambda: predecessor.verify_any_checkpoint(path))


def verify_checkpoint(path: Path):
    return _with_stale_errors(lambda: predecessor.verify_checkpoint(path))


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path):
        return _with_stale_errors(
            lambda: predecessor.scene_checkpoint.verify_or_standard(path)
        )


scene_checkpoint = _CheckpointVerifier()


def main() -> int:
    return int(_with_stale_errors(predecessor.main))


if __name__ == "__main__":
    raise SystemExit(main())

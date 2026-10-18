#!/usr/bin/env python3
"""Allow recovery from the exact nested-quarantine namespace collision."""

from pathlib import Path

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v5 as predecessor


QUARANTINE_COLLISION_ERRORS = {
    f"RuntimeError: xp_cpu_w{worker} quarantine target already exists"
    for worker in (1, 2)
} | {
    f"RuntimeError: xp_io_w{worker} quarantine target already exists"
    for worker in (1, 2)
}


def _with_collision_errors(call):
    old = set(predecessor.STALE_TASK_ERRORS)
    predecessor.STALE_TASK_ERRORS.update(QUARANTINE_COLLISION_ERRORS)
    try:
        return call()
    finally:
        predecessor.STALE_TASK_ERRORS.clear()
        predecessor.STALE_TASK_ERRORS.update(old)


def verify_any_checkpoint(path: Path):
    return _with_collision_errors(lambda: predecessor.verify_any_checkpoint(path))


def verify_checkpoint(path: Path):
    return _with_collision_errors(lambda: predecessor.verify_checkpoint(path))


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path):
        return _with_collision_errors(
            lambda: predecessor.scene_checkpoint.verify_or_standard(path)
        )


scene_checkpoint = _CheckpointVerifier()


def main() -> int:
    return int(_with_collision_errors(predecessor.main))


if __name__ == "__main__":
    raise SystemExit(main())


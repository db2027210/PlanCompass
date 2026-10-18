#!/usr/bin/env python3
"""Build replay quarantine for the XP-I/O instability checkpoint."""

from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as base
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v8 as checkpoint


class _Verifier:
    @staticmethod
    def verify_checkpoint(path):
        return checkpoint.scene_checkpoint.verify_or_standard(path)


def main() -> int:
    old = base.verifier
    base.verifier = _Verifier()
    try:
        return int(base.main())
    finally:
        base.verifier = old


if __name__ == "__main__":
    raise SystemExit(main())


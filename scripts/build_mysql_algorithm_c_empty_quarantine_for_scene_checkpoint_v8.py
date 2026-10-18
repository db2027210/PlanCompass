#!/usr/bin/env python3
"""Build an empty replay quarantine for a verified v12 scene checkpoint."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as base
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as checkpoint


class _Verifier:
    @staticmethod
    def verify_checkpoint(path):
        return checkpoint.verify_checkpoint(path)


def main() -> int:
    old = base.verifier
    base.verifier = _Verifier()
    try:
        return int(base.main())
    finally:
        base.verifier = old


if __name__ == "__main__":
    raise SystemExit(main())


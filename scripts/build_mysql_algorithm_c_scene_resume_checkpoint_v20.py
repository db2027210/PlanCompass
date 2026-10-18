#!/usr/bin/env python3
"""Build v19 scene checkpoints when the predecessor is itself schema v18."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v18 as v18
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v19 as v19


def _verify_inherited(path: Path):
    value = v19._load(path)
    if value.get("schema_version") == v19.SCHEMA:
        return v19.verify_checkpoint(path)
    if value.get("schema_version") == v18.SCHEMA:
        return v18.verify_checkpoint(path)
    return v18._verify_inherited(path)


def _with_compatibility(callback, *args):
    original = v19._verify_inherited
    original_standard = v19._STANDARD_VERIFY
    def verify_base(path: Path):
        value = v19._load(path)
        if value.get("schema_version") == v18.SCHEMA:
            return v18.verify_checkpoint(path)
        return original_standard(path)
    v19._verify_inherited = _verify_inherited
    v19._STANDARD_VERIFY = verify_base
    try:
        return callback(*args)
    finally:
        v19._STANDARD_VERIFY = original_standard
        v19._verify_inherited = original


def verify_checkpoint(path: Path):
    return _with_compatibility(v19.verify_checkpoint, path)


def main() -> int:
    return int(_with_compatibility(v19.main))


if __name__ == "__main__":
    raise SystemExit(main())

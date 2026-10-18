#!/usr/bin/env python3
"""Build a chained scene checkpoint whose predecessor may itself be v17."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v17 as v17


def _verify_inherited(path: Path):
    value = v17._load(path)
    if value.get("schema_version") == v17.SCHEMA:
        return v17.verify_checkpoint(path)
    return v17._verify_inherited(path)


_source = v17._source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v17"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v18"',
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

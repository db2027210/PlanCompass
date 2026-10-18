#!/usr/bin/env python3
"""Build a chained scene checkpoint whose predecessor may itself be v16."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v11 as v11
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as v12
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v13 as v13
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v14 as v14
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v15 as v15
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v16 as v16


def _verify_inherited(path: Path):
    value = v16._load(path)
    for module in (v16, v15, v14, v13, v12, v11):
        if value.get("schema_version") == module.SCHEMA:
            return module.verify_checkpoint(path)
    return v16._verify_inherited(path)


_source = v16._source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v16"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v17"',
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

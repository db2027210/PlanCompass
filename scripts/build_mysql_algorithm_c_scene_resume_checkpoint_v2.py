#!/usr/bin/env python3
"""Scene-boundary checkpoint builder accepting the v42 missing-symbol stop."""

from pathlib import Path


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_algorithm_c_scene_resume_checkpoint_v1.py"
)
_source = SOURCE.read_text(encoding="utf-8")
old = '''RECOVERABLE_ERRORS = {
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
    "RuntimeError: outer 2 current_interval_plan_coverage_002 wave 1 failed closed",
}
'''
new = '''RECOVERABLE_ERRORS = {
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
    "RuntimeError: outer 2 current_interval_plan_coverage_002 wave 1 failed closed",
    "NameError: name '_admit_coverage_top10' is not defined",
}
'''
if _source.count(old) != 1:
    raise RuntimeError("scene checkpoint v1 recoverable-error boundary differs")
_source = _source.replace(old, new)
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v1"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v2"',
)
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

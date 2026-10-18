#!/usr/bin/env python3
"""Build a chained zero-credit checkpoint after a cleanup failure."""

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


def _verify_inherited(path: Path):
    value = v14._load(path)
    if value.get("schema_version") == v14.SCHEMA:
        return v14.verify_checkpoint(path)
    if value.get("schema_version") == v13.SCHEMA:
        return v13.verify_checkpoint(path)
    if value.get("schema_version") == v12.SCHEMA:
        return v12.verify_checkpoint(path)
    if value.get("schema_version") == v11.SCHEMA:
        return v11.verify_checkpoint(path)
    return v14._verify_inherited(path)


_source = v14._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v14"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v15"',
)

_old_failure_flags = '''    technical_invalid_failure = (
        state.get("error")
        == "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
    )
    inherited_reconstructed: Mapping[str, Any] = {}
    if technical_invalid_failure:
'''
_new_failure_flags = '''    technical_invalid_failure = (
        state.get("error")
        == "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
    )
    chained_zero_credit_failure = (
        int(state.get("completed_predecessor_outer_attempts") or 0) > 0
        and int(state.get("completed_predecessor_outer_attempts") or 0)
        == int(state["outer_attempt"])
    )
    inherited_reconstructed: Mapping[str, Any] = {}
    if chained_zero_credit_failure:
'''
if _source.count(_old_failure_flags) != 1:
    raise RuntimeError("scene checkpoint v15 chained-failure boundary differs")
_source = _source.replace(_old_failure_flags, _new_failure_flags)

# The v14 fallback and lineage binding were restricted to an all-timeout
# failure.  A chained cleanup timeout at the same completed predecessor has
# the same zero-credit state boundary and must use the same inherited state.
if _source.count("if technical_invalid_failure") != 4:
    raise RuntimeError("scene checkpoint v15 fallback count differs")
_source = _source.replace(
    "if technical_invalid_failure", "if chained_zero_credit_failure"
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

#!/usr/bin/env python3
"""Build a resume-entry-compatible checkpoint with local completed outers."""

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


def _verify_inherited(path: Path):
    value = v15._load(path)
    for module in (v15, v14, v13, v12, v11):
        if value.get("schema_version") == module.SCHEMA:
            return module.verify_checkpoint(path)
    return v15._verify_inherited(path)


_source = v15._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v15"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v16"',
)

_old_inherited = '''    inherited_reconstructed: Mapping[str, Any] = {}
    if chained_zero_credit_failure:
        inherited_path = Path(str(state.get("checkpoint_path") or "")).resolve()
        inherited = _verify_inherited(inherited_path)
        if int(inherited.get("completed_outer_attempts") or -1) != int(
            state["outer_attempt"]
        ):
            raise RuntimeError("technical-invalid predecessor boundary differs")
        inherited_reconstructed = inherited.get("reconstructed_state") or {}
'''
_new_inherited = '''    inherited_reconstructed: Mapping[str, Any] = {}
    inherited: Mapping[str, Any] = {}
    declared_predecessor = int(
        state.get("completed_predecessor_outer_attempts") or 0
    )
    if declared_predecessor > 0:
        inherited_path = Path(str(state.get("checkpoint_path") or "")).resolve()
        inherited = _verify_inherited(inherited_path)
        if int(inherited.get("completed_outer_attempts") or -1) != declared_predecessor:
            raise RuntimeError("inherited predecessor boundary differs")
        if chained_zero_credit_failure:
            inherited_reconstructed = inherited.get("reconstructed_state") or {}
'''
if _source.count(_old_inherited) != 1:
    raise RuntimeError("scene checkpoint v16 inherited-state boundary differs")
_source = _source.replace(_old_inherited, _new_inherited)

_old_lineage = '''    if chained_zero_credit_failure:
        lineage = (inherited.get("evidence") or {}).get("source_state") or {}
'''
_new_lineage = '''    if declared_predecessor > 0:
        lineage = (inherited.get("evidence") or {}).get("source_state") or {}
'''
if _source.count(_old_lineage) != 1:
    raise RuntimeError("scene checkpoint v16 lineage boundary differs")
_source = _source.replace(_old_lineage, _new_lineage)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

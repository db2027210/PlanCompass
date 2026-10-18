#!/usr/bin/env python3
"""Build a chained TPC-H timeout checkpoint accepted by the resume entry."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v11 as v11
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as v12
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v13 as v13


def _verify_inherited(path: Path):
    value = v13._load(path)
    if value.get("schema_version") == v13.SCHEMA:
        return v13.verify_checkpoint(path)
    if value.get("schema_version") == v12.SCHEMA:
        return v12.verify_checkpoint(path)
    if value.get("schema_version") == v11.SCHEMA:
        return v11.verify_checkpoint(path)
    return v13._verify_inherited(path)


_source = v13._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v13"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v14"',
)

_old_source_evidence = '''    source_state_path = run_root / "run_state.json"
    document: dict[str, Any] = {
'''
_new_source_evidence = '''    source_state_path = run_root / "run_state.json"
    resume_entry_source_state_path = source_state_path
    immediate_failed_source_state = None
    if technical_invalid_failure:
        lineage = (inherited.get("evidence") or {}).get("source_state") or {}
        resume_entry_source_state_path = Path(str(lineage.get("path") or "")).resolve()
        if (
            not resume_entry_source_state_path.is_file()
            or resume_entry_source_state_path.is_symlink()
            or _sha(resume_entry_source_state_path) != lineage.get("sha256")
        ):
            raise RuntimeError("technical-invalid resume lineage source differs")
        immediate_failed_source_state = _bound(source_state_path)
    document: dict[str, Any] = {
'''
if _source.count(_old_source_evidence) != 1:
    raise RuntimeError("scene checkpoint v14 source-evidence boundary differs")
_source = _source.replace(_old_source_evidence, _new_source_evidence)

_old_source_binding = '            "source_state": _bound(source_state_path),\n'
_new_source_binding = '''            "source_state": _bound(resume_entry_source_state_path),
            "immediate_failed_source_state": immediate_failed_source_state,
'''
if _source.count(_old_source_binding) != 1:
    raise RuntimeError("scene checkpoint v14 source-binding boundary differs")
_source = _source.replace(_old_source_binding, _new_source_binding)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

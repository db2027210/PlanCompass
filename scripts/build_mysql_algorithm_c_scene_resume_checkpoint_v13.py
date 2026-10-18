#!/usr/bin/env python3
"""Build a cap-retired checkpoint after an all-technical-invalid outer."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v11 as v11
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as v12


def _verify_inherited(path: Path):
    value = v12._load(path)
    if value.get("schema_version") == v12.SCHEMA:
        return v12.verify_checkpoint(path)
    if value.get("schema_version") == v11.SCHEMA:
        return v11.verify_checkpoint(path)
    return v12._verify_inherited(path)


_source = v12._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v12"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v13"',
)
_source = _source.replace(
    '    "RuntimeError: post-target confirmation counter exceeds bound",\n',
    '    "RuntimeError: post-target confirmation counter exceeds bound",\n'
    '    "RuntimeError: Algorithm C candidate batch incomplete or technically invalid",\n',
)

_old_state_copy = '''    reconstructed = copy.deepcopy(base["reconstructed_state"])
    scene_fields = (
        "incumbents", "params_by_scene", "ranges_by_scene", "accepted_rounds",
        "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    for field in scene_fields:
        source = state.get(field)
        if not isinstance(source, Mapping) or scene not in source:
            raise RuntimeError(f"source state field differs: {field}")
        reconstructed[field][scene] = copy.deepcopy(source[scene])
    reconstructed["active_by_scene"][scene] = list(
        state["ranges_by_scene"][scene]
    )
'''
_new_state_copy = '''    reconstructed = copy.deepcopy(base["reconstructed_state"])
    technical_invalid_failure = (
        state.get("error")
        == "RuntimeError: Algorithm C candidate batch incomplete or technically invalid"
    )
    inherited_reconstructed: Mapping[str, Any] = {}
    if technical_invalid_failure:
        inherited_path = Path(str(state.get("checkpoint_path") or "")).resolve()
        inherited = _verify_inherited(inherited_path)
        if int(inherited.get("completed_outer_attempts") or -1) != int(
            state["outer_attempt"]
        ):
            raise RuntimeError("technical-invalid predecessor boundary differs")
        inherited_reconstructed = inherited.get("reconstructed_state") or {}
    scene_fields = (
        "incumbents", "params_by_scene", "ranges_by_scene", "accepted_rounds",
        "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    for field in scene_fields:
        source = (
            inherited_reconstructed.get(field)
            if technical_invalid_failure
            else state.get(field)
        )
        if not isinstance(source, Mapping) or scene not in source:
            raise RuntimeError(f"source state field differs: {field}")
        reconstructed[field][scene] = copy.deepcopy(source[scene])
    active_ranges = (
        inherited_reconstructed.get("ranges_by_scene")
        if technical_invalid_failure
        else state.get("ranges_by_scene")
    )
    reconstructed["active_by_scene"][scene] = list(active_ranges[scene])
'''
if _source.count(_old_state_copy) != 1:
    raise RuntimeError("scene checkpoint v13 state-copy boundary differs")
_source = _source.replace(_old_state_copy, _new_state_copy)

_old_archives = '''        reconstructed.setdefault(field, {})
        reconstructed[field][scene] = copy.deepcopy((state.get(field) or {})[scene])
'''
_new_archives = '''        reconstructed.setdefault(field, {})
        archive_source = (
            inherited_reconstructed.get(field)
            if technical_invalid_failure
            else state.get(field)
        )
        reconstructed[field][scene] = copy.deepcopy(
            (archive_source or {})[scene]
        )
'''
if _source.count(_old_archives) != 1:
    raise RuntimeError("scene checkpoint v13 archive-copy boundary differs")
_source = _source.replace(_old_archives, _new_archives)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

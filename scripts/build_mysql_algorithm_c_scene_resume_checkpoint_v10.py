#!/usr/bin/env python3
"""Build a scene checkpoint after a fresh-search dynamic-gate failure.

The v4 builder handles chained checkpoints, but treated the historical
bootstrap checkpoint as completed predecessor work even when the controller
had explicitly reset that history with ``--fresh-search``.  This version
uses ``completed_predecessor_outer_attempts`` as the authority: a zero value
means that all decisions in the failed run are local to the fresh search.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v1 as checkpoint_v1
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v2 as checkpoint_v2


def _verify_inherited(path: Path):
    value = checkpoint_v2._load(path)
    if value.get("schema_version") == checkpoint_v2.SCHEMA:
        return checkpoint_v2.verify_checkpoint(path)
    if value.get("schema_version") == checkpoint_v1.SCHEMA:
        return checkpoint_v1.verify_checkpoint(path)
    return checkpoint_v2._STANDARD_VERIFY(path)


SOURCE = Path(__file__).resolve().with_name(
    "build_mysql_algorithm_c_scene_resume_checkpoint_v1.py"
)
_source = SOURCE.read_text(encoding="utf-8")
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v1"',
        'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v10"',
)

_old_baseline_binding = '            "baseline": copy.deepcopy(base["evidence"]["baseline"]),'
_new_baseline_binding = '''            "baseline": _bound(Path(str(
                (state.get("formal_incumbent_baseline") or {}).get("path") or ""
            ))),'''
if _source.count(_old_baseline_binding) != 1:
    raise RuntimeError("scene checkpoint v10 formal-baseline boundary differs")
_source = _source.replace(_old_baseline_binding, _new_baseline_binding)
_source = _source.replace(
    '''RECOVERABLE_ERRORS = {
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
    "RuntimeError: outer 2 current_interval_plan_coverage_002 wave 1 failed closed",
}
''',
    '''RECOVERABLE_ERRORS = {
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt",
    "RuntimeError: outer 2 current_interval_plan_coverage_002 wave 1 failed closed",
    "NameError: name '_admit_coverage_top10' is not defined",
    "RuntimeError: dynamic non-interference gate failed closed",
}
''',
)

_old_decision_chain = '''    decisions: list[dict[str, Any]] = []
    accepted = rejected = 0
    last_valid_keys: set[str] = set()
    for outer in range(1, completed + 1):
        path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
        row = _load(path)
        if (
            row.get("scene") != scene
            or int(row.get("outer_attempt") or 0) != outer
            or row.get("formal_evidence_eligible") is not True
            or not isinstance(row.get("accepted"), bool)
        ):
            raise RuntimeError(f"completed outer decision differs: {path}")
        accepted += int(row["accepted"])
        rejected += int(not row["accepted"])
        if outer == completed and not row["accepted"]:
            for evaluation in row.get("evaluations") or []:
                if (
                    isinstance(evaluation, Mapping)
                    and evaluation.get("hard_gate_passed") is True
                    and evaluation.get("coverage_complete") is True
                    and isinstance(evaluation.get("params"), Mapping)
                ):
                    last_valid_keys.add(_optimizer_key(evaluation["params"]))
        decisions.append({"outer_attempt": outer, **_bound(path)})
    if (
        accepted != int((state.get("accepted_rounds") or {}).get(scene, -1))
        or rejected != int((state.get("rejected_outer_attempts") or {}).get(scene, -1))
    ):
        raise RuntimeError("source completed decision counts differ")
'''

_new_decision_chain = '''    decisions: list[dict[str, Any]] = []
    accepted = rejected = inherited_completed = 0
    declared_inherited = int(
        state.get("completed_predecessor_outer_attempts") or 0
    )
    inherited_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    if declared_inherited > 0:
        if not inherited_path.is_file():
            raise RuntimeError("declared inherited checkpoint is missing")
        inherited = _verify_inherited(inherited_path)
        inherited_completed = int(inherited.get("completed_outer_attempts") or 0)
        inherited_state = inherited.get("reconstructed_state") or {}
        if (
            inherited_completed != declared_inherited
            or inherited_completed > completed
            or inherited.get("scene") not in {None, scene}
        ):
            raise RuntimeError("inherited scene checkpoint boundary differs")
        accepted = int(
            (inherited_state.get("accepted_rounds") or {}).get(scene, 0)
        )
        rejected = int(
            (inherited_state.get("rejected_outer_attempts") or {}).get(scene, 0)
        )
        decisions.append({
            "inherited_through_outer": inherited_completed,
            **_bound(inherited_path),
        })
    elif state.get("fresh_search") is not True:
        raise RuntimeError("zero predecessor boundary requires fresh_search")
    last_valid_keys: set[str] = set()
    for outer in range(inherited_completed + 1, completed + 1):
        path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
        row = _load(path)
        if (
            row.get("scene") != scene
            or int(row.get("outer_attempt") or 0) != outer
            or row.get("formal_evidence_eligible") is not True
            or not isinstance(row.get("accepted"), bool)
        ):
            raise RuntimeError(f"completed outer decision differs: {path}")
        accepted += int(row["accepted"])
        rejected += int(not row["accepted"])
        if outer == completed and not row["accepted"]:
            for evaluation in row.get("evaluations") or []:
                if (
                    isinstance(evaluation, Mapping)
                    and evaluation.get("hard_gate_passed") is True
                    and evaluation.get("coverage_complete") is True
                    and isinstance(evaluation.get("params"), Mapping)
                ):
                    last_valid_keys.add(_optimizer_key(evaluation["params"]))
        decisions.append({"outer_attempt": outer, **_bound(path)})
    if (
        accepted != int((state.get("accepted_rounds") or {}).get(scene, -1))
        or rejected != int((state.get("rejected_outer_attempts") or {}).get(scene, -1))
    ):
        raise RuntimeError("source completed decision counts differ")
'''

if _source.count(_old_decision_chain) != 1:
    raise RuntimeError("scene checkpoint v10 decision-chain boundary differs")
_source = _source.replace(_old_decision_chain, _new_decision_chain)
_source = _source.replace(
    '    if scene == "tpch_sf4_memory":\n',
    '    if (\n'
    '        scene == "tpch_sf4_memory"\n'
    '        and str(state.get("error") or "").startswith(\n'
    '            "RuntimeError: outer 2 current_interval_plan_coverage_002"\n'
    '        )\n'
    '    ):\n',
    1,
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

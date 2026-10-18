#!/usr/bin/env python3
"""Build a scene checkpoint after retiring the legacy accepted-round cap.

The failed source may have valid accepted decisions beyond the historical
counter value 10.  This builder restores the actual number of accepted outer
decisions, clears the obsolete post-target confirmation counter, and grants no
credit to the incomplete following outer.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v11 as v11


_verify_inherited = v11._verify_inherited
_source = v11._source
_source = _source.replace(
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v11"',
    'SCHEMA = "mysql_algorithm_c_single_scene_outer_resume_checkpoint_v12"',
)
_source = _source.replace(
    '    "RuntimeError: dynamic non-interference gate failed closed",\n',
    '    "RuntimeError: dynamic non-interference gate failed closed",\n'
    '    "RuntimeError: post-target confirmation counter exceeds bound",\n',
)

_old_counts = '''    decisions: list[dict[str, Any]] = []
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

_new_counts = '''    decisions: list[dict[str, Any]] = []
    accepted = credited_accepted = rejected = inherited_completed = 0
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
        accepted = credited_accepted = int(
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
        credit = row.get("accepted_state_credit_increment")
        credit = int(row["accepted"]) if credit is None else int(credit)
        if credit not in {0, 1} or (not row["accepted"] and credit != 0):
            raise RuntimeError(f"completed outer accepted credit differs: {path}")
        credited_accepted += credit
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
    source_accepted = int((state.get("accepted_rounds") or {}).get(scene, -1))
    legacy_cap_failure = (
        state.get("error")
        == "RuntimeError: post-target confirmation counter exceeds bound"
    )
    if (
        rejected != int((state.get("rejected_outer_attempts") or {}).get(scene, -1))
        or (
            legacy_cap_failure
            and not (
                source_accepted == 10
                and credited_accepted == 10
                and accepted > source_accepted
            )
        )
        or (not legacy_cap_failure and accepted != source_accepted)
    ):
        raise RuntimeError("source completed decision counts differ")
    state["_resume_actual_accepted_decisions"] = accepted
    state["_resume_legacy_accepted_credit"] = credited_accepted
'''
if _source.count(_old_counts) != 1:
    raise RuntimeError("scene checkpoint v12 decision-count boundary differs")
_source = _source.replace(_old_counts, _new_counts)

_old_reconstructed = '''    reconstructed.setdefault("post_target_stability_confirmation_counts", {})
    reconstructed["post_target_stability_confirmation_counts"][scene] = int(
        (state.get("post_target_stability_confirmation_counts") or {}).get(scene, 0)
    )
'''
_new_reconstructed = '''    legacy_actual_accepted = int(
        state.pop("_resume_actual_accepted_decisions")
    )
    legacy_credited_accepted = int(
        state.pop("_resume_legacy_accepted_credit")
    )
    if state.get("error") == "RuntimeError: post-target confirmation counter exceeds bound":
        reconstructed["accepted_rounds"][scene] = legacy_actual_accepted
    reconstructed["post_target_stability_confirmation_counts"] = {}
'''
if _source.count(_old_reconstructed) != 1:
    raise RuntimeError("scene checkpoint v12 legacy-counter boundary differs")
_source = _source.replace(_old_reconstructed, _new_reconstructed)

_old_audit = '''            "accepted_history_preserved": True,
            "replay_archive_preserved": True,
            "source_error": state["error"],
'''
_new_audit = '''            "accepted_history_preserved": True,
            "replay_archive_preserved": True,
            "legacy_accepted_credit": legacy_credited_accepted,
            "actual_accepted_decisions": legacy_actual_accepted,
            "legacy_post_target_confirmation_counter_retired": (
                state.get("error")
                == "RuntimeError: post-target confirmation counter exceeds bound"
            ),
            "source_error": state["error"],
'''
if _source.count(_old_audit) != 1:
    raise RuntimeError("scene checkpoint v12 audit boundary differs")
_source = _source.replace(_old_audit, _new_audit)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())


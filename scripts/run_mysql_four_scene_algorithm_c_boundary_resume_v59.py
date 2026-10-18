#!/usr/bin/env python3
"""Exclude permanent-invalid identities from every PG recovery candidate path."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v3 as interval_control
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v58 as v58


scene_checkpoint = v58.scene_checkpoint
_source = v58._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v59 {label} boundary differs")
    _source = _source.replace(old, new)


_replace_once(
    '''                            replayed_signatures=replayed[scene],
                            accepted_signatures=set(state[''',
    '''                            replayed_signatures=replayed[scene],
                            permanently_invalid_signatures=permanently_invalid[scene],
                            accepted_signatures=set(state[''',
    "first-miss permanent-invalid exclusion",
)

recovery_admission = '''                        selected, admission = pg_outer.admit_partial_topk(
                            scored,
                            incumbent_params=params_by_scene[scene],'''
filtered_recovery_admission = '''                        scored, recovery_identity_exclusion = (
                            interval_control.filter_recovery_rankings(
                                scored,
                                replayed_signatures=replayed[scene],
                                permanently_invalid_signatures=permanently_invalid[scene],
                                optimizer_params_key=alg.optimizer_params_key,
                            )
                        )
                        selected, admission = pg_outer.admit_partial_topk(
                            scored,
                            incumbent_params=params_by_scene[scene],'''
if _source.count(recovery_admission) != 1:
    raise RuntimeError("Algorithm C v59 primary recovery admission boundary differs")
_source = _source.replace(recovery_admission, filtered_recovery_admission)

fallback_admission = '''                            selected, admission = pg_outer.admit_partial_topk(
                                scored,
                                incumbent_params=params_by_scene[scene],'''
filtered_fallback_admission = '''                            scored, recovery_identity_exclusion = (
                                interval_control.filter_recovery_rankings(
                                    scored,
                                    replayed_signatures=replayed[scene],
                                    permanently_invalid_signatures=permanently_invalid[scene],
                                    optimizer_params_key=alg.optimizer_params_key,
                                )
                            )
                            selected, admission = pg_outer.admit_partial_topk(
                                scored,
                                incumbent_params=params_by_scene[scene],'''
if _source.count(fallback_admission) != 1:
    raise RuntimeError("Algorithm C v59 fallback recovery admission boundary differs")
_source = _source.replace(fallback_admission, filtered_fallback_admission)

_replace_once(
    '''                            "generation": recovery_audit,
                            "admission": admission,''',
    '''                            "generation": recovery_audit,
                            "identity_exclusion": recovery_identity_exclusion,
                            "admission": admission,''',
    "recovery exclusion audit",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())

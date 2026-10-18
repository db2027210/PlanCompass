#!/usr/bin/env python3
"""Run current PG-C logic with external processes recorded but nonblocking."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_external_process_inventory_only_gate_v1 as external_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v59 as v59


scene_checkpoint = v59.scene_checkpoint
_source = v59._source


def _replace(old: str, new: str, expected: int, label: str) -> None:
    global _source
    if _source.count(old) != expected:
        raise RuntimeError(f"Algorithm C v60 {label} boundary differs")
    _source = _source.replace(old, new)


def external_process_inventory_only_validate_authority(original: object, r0mod: object):
    return external_gate.validate_point_authority(original, r0mod)


_replace(
    "freeze, workers, point_gate = alg.r0mod._validate_authority()",
    "freeze, workers, point_gate = external_process_inventory_only_validate_authority(alg.r0mod._validate_authority, alg.r0mod)",
    2,
    "point authority",
)
_replace(
    "coordination.rolling_quiet_gate(",
    "external_gate.rolling_quiet_gate(",
    2,
    "per-wave gate",
)
_replace(
    '"formal_evidence_eligible": not pilot,',
    '"formal_evidence_eligible": False,\n'
    '        "strict_external_noninterference_evidence_eligible": False,\n'
    '        "external_processes_inventory_only": True,\n'
    '        "external_processes_can_block": False,\n'
    '        "external_process_policy_release": external_gate.RELEASE,',
    1,
    "state evidence classification",
)
_replace(
    '"formal_evidence_eligible": not args.interference_override_pilot,',
    '"formal_evidence_eligible": False,\n'
    '                "strict_external_noninterference_evidence_eligible": False,',
    3,
    "artifact evidence classification",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
